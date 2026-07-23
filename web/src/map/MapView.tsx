import maplibregl, { Map as MLMap } from "maplibre-gl";
import { Protocol } from "pmtiles";
import { useEffect, useRef, useState } from "react";
import { CONUS_BOUNDS } from "../config";

// Serve PMTiles straight from object storage via range requests.
const protocol = new Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const BASEMAP_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
      maxzoom: 19,
    },
  },
  layers: [
    { id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.6, "raster-opacity": 0.9 } },
  ],
};

const THEMATIC_SOURCE = "thematic";
const THEMATIC_LAYER = "thematic-raster";
const CONUS_CENTER: [number, number] = [-96.9, 38.5];
// Viewport framing per layer family: US layers stay tightly framed on CONUS
// with a strong world-mask; hurricane layers relax to the full basin with a
// light mask so ocean context reads normally.
const VIEWPORTS = {
  conus: {
    bounds: [[-135, 17], [-56, 56]] as [[number, number], [number, number]],
    minZoom: 3.2,
    maskOpacity: 0.75,
  },
  basin: {
    bounds: [[-140, 5], [-40, 57]] as [[number, number], [number, number]],
    minZoom: 2.7,
    // No mask at basin scale — its rectangular CONUS hole reads as a broken
    // box over open ocean.
    maskOpacity: 0,
  },
};

export interface Pin {
  lat: number;
  lon: number;
  label: string;
}

export interface VectorSpec {
  url: string;
  geometry: string; // advisory — all geometry types render via typed sublayers
  colorProp: string;
  hover: string[];
  /** Property whose shared value groups features (e.g. one storm's segments)
   *  so hover/select lights the WHOLE group, not one segment. */
  groupProp?: string;
  colors: { value: string | number; color: string }[];
  fillOpacity?: number;
  circleRadius?: number;
}

const VECTOR_SOURCE = "vector-src";
// One source, three geometry-typed sublayers — mixed collections (storm cone
// polygons + track lines + position points) render together.
const VECTOR_LAYERS = ["vec-fill", "vec-outline", "vec-line", "vec-circle"] as const;

interface Props {
  tilesUrl: string | null;
  vector?: VectorSpec | null;
  external?: { tiles: string[]; attribution: string; maxzoom: number; opacity: number } | null;
  featureFilter?: unknown[][] | null;
  viewport?: "conus" | "basin";
  animFrames?: string[] | null;
  animFrame?: number;
  rasterOpacity?: number;
  maxzoom: number;
  initialView: { zoom?: number; center?: [number, number] };
  onViewChange: (zoom: number, center: [number, number]) => void;
  sample?: (lng: number, lat: number) => Promise<string>;
  pins?: Pin[];
  onClickInfo?: (info: { lat: number; lng: number; feature: Record<string, unknown> | null }) => void;
}

export function MapView({ tilesUrl, vector, external, featureFilter, viewport = "conus", animFrames = null, animFrame = 5, rasterOpacity, maxzoom, initialView, onViewChange, sample, pins, onClickInfo }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const [map, setMap] = useState<MLMap | null>(null);
  const [styleReady, setStyleReady] = useState(false);
  const sampleRef = useRef(sample);
  sampleRef.current = sample;
  const vectorRef = useRef(vector);
  vectorRef.current = vector;
  const clickInfoRef = useRef(onClickInfo);
  clickInfoRef.current = onClickInfo;

  // Create the map only once the container has real dimensions.
  // Constructing MapLibre against a 0×0 container (or with `bounds` at
  // construction time) throws "failed to invert matrix".
  useEffect(() => {
    const el = container.current;
    if (!el) return;
    let cancelled = false;
    let raf = 0;
    let created: MLMap | null = null;

    const boot = () => {
      if (cancelled) return;
      if (el.clientWidth === 0 || el.clientHeight === 0) {
        raf = requestAnimationFrame(boot);
        return;
      }
      const m = new maplibregl.Map({
        container: el,
        style: BASEMAP_STYLE,
        center: initialView.center
          ? [initialView.center[1], initialView.center[0]]
          : CONUS_CENTER,
        zoom: initialView.zoom ?? 3.4,
        minZoom: VIEWPORTS.conus.minZoom,
        maxBounds: VIEWPORTS.conus.bounds,
        attributionControl: { compact: true },
      });
      m.once("load", () => {
        // Wash out everything outside CONUS — this app is US-scoped.
        const [w, s] = [-125.0208333, 24.0625];
        const [e2, n] = [-66.4791667, 49.9375];
        m.addSource("world-mask", {
          type: "geojson",
          data: {
            type: "Feature",
            properties: {},
            geometry: {
              type: "Polygon",
              coordinates: [
                [[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]],
                [[w, s], [e2, s], [e2, n], [w, n], [w, s]],
              ],
            },
          },
        });
        m.addLayer({
          id: "world-mask",
          type: "fill",
          source: "world-mask",
          paint: { "fill-color": "#e8ebee", "fill-opacity": 0.75 },
        });
        setStyleReady(true);
      });
      if (!initialView.center && initialView.zoom === undefined) {
        try {
          m.fitBounds(CONUS_BOUNDS, { padding: 24, duration: 0 });
        } catch {
          /* keep the fallback center/zoom */
        }
      }
      m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      m.addControl(
        new maplibregl.GeolocateControl({
          positionOptions: { enableHighAccuracy: false },
          trackUserLocation: false,
        }),
        "top-right"
      );

      const popup = new maplibregl.Popup({ closeButton: true, closeOnClick: false });

      // --- Interaction feel ---------------------------------------------
      // Proximity hover: features light up when the cursor is NEAR them
      // (±8 px), not only when it's pixel-perfect on a 4 px dot. rAF-throttled.
      let hoverIds: (number | string)[] = [];
      let selectedIds: (number | string)[] = [];
      let lastHoverTop: number | string | undefined;
      // A feature expands to its whole GROUP (e.g. every segment of one
      // storm) when the layer declares a groupProp — segments keep their own
      // intensity colors; the entire path thickens/brightens together.
      const expand = (f: maplibregl.MapGeoJSONFeature | undefined): (number | string)[] => {
        if (!f || f.id === undefined) return [];
        const gp = vectorRef.current?.groupProp;
        const gv = gp ? (f.properties as any)?.[gp] : undefined;
        if (gp && gv !== undefined && gv !== null && m.getSource(VECTOR_SOURCE)) {
          try {
            const feats = m.querySourceFeatures(VECTOR_SOURCE, {
              filter: ["==", ["get", gp], gv] as any,
            });
            const ids = [...new Set(feats.map((q) => q.id).filter((x) => x !== undefined))];
            if (ids.length) return ids as (number | string)[];
          } catch {
            /* fall through to single id */
          }
        }
        return [f.id];
      };
      const applyState = (
        ids: (number | string)[],
        key: "hover" | "selected",
        prev: (number | string)[]
      ): (number | string)[] => {
        if (!m.getSource(VECTOR_SOURCE)) return prev;
        try {
          const next = new Set(ids);
          for (const id of prev)
            if (!next.has(id)) m.setFeatureState({ source: VECTOR_SOURCE, id }, { [key]: false });
          for (const id of ids)
            m.setFeatureState({ source: VECTOR_SOURCE, id }, { [key]: true });
        } catch {
          /* source mid-swap */
        }
        return ids;
      };
      let rafPending = false;
      let lastMove: maplibregl.MapMouseEvent | null = null;
      m.on("mousemove", (e) => {
        lastMove = e;
        if (rafPending) return;
        rafPending = true;
        requestAnimationFrame(() => {
          rafPending = false;
          const ev = lastMove;
          if (!ev) return;
          const present = VECTOR_LAYERS.filter((id) => m.getLayer(id));
          if (!present.length) return;
          const feats = m.queryRenderedFeatures(
            [
              [ev.point.x - 8, ev.point.y - 8],
              [ev.point.x + 8, ev.point.y + 8],
            ],
            { layers: present as unknown as string[] }
          );
          m.getCanvas().style.cursor = feats.length ? "pointer" : "";
          const top = feats[0]?.id;
          if (top !== lastHoverTop) {
            lastHoverTop = top;
            hoverIds = applyState(expand(feats[0]), "hover", hoverIds);
          } else if (!feats.length && hoverIds.length) {
            hoverIds = applyState([], "hover", hoverIds);
          }
        });
      });
      // Clicking a feature marks it "selected" (pops visually) until the
      // popup closes or the next click lands elsewhere.
      popup.on("close", () => {
        selectedIds = applyState([], "selected", selectedIds);
      });

      m.on("click", async (e) => {
        // Vector layers: show the clicked feature's properties.
        const v = vectorRef.current;
        const present = VECTOR_LAYERS.filter((id) => m.getLayer(id));
        if (v && present.length) {
          // Generous hitbox — 4 px dots shouldn't demand needle-threading.
          const feats = m.queryRenderedFeatures(
            [
              [e.point.x - 9, e.point.y - 9],
              [e.point.x + 9, e.point.y + 9],
            ],
            { layers: present as unknown as string[] }
          );
          selectedIds = applyState(expand(feats[0]), "selected", selectedIds);
          if (feats.length) {
            const p = feats[0].properties ?? {};
            clickInfoRef.current?.({ lat: e.lngLat.lat, lng: e.lngLat.lng, feature: p });
            const rows = v.hover
              .filter((k) => p[k] !== undefined && p[k] !== null && p[k] !== "")
              .map((k) => `<b>${k.replace(/_/g, " ")}:</b> ${p[k]}`)
              .join("<br/>");
            popup.setLngLat(e.lngLat).setHTML(`<div class="popup">${rows}</div>`).addTo(m);
            // Append the local mini-forecast below the feature details.
            if (sampleRef.current) {
              const extra = await sampleRef.current(e.lngLat.lng, e.lngLat.lat).catch(() => "");
              if (extra && popup.isOpen()) {
                popup.setHTML(`<div class="popup">${rows}<div class="fc-sep"></div>${extra}</div>`);
              }
            }
            return;
          }
        }
        clickInfoRef.current?.({ lat: e.lngLat.lat, lng: e.lngLat.lng, feature: null });
        const coords = `${e.lngLat.lat.toFixed(3)}, ${e.lngLat.lng.toFixed(3)}`;
        popup
          .setLngLat(e.lngLat)
          .setHTML(`<div class="popup"><span class="popup-loading">Loading forecast…</span></div>`)
          .addTo(m);
        const text = sampleRef.current
          ? await sampleRef.current(e.lngLat.lng, e.lngLat.lat).catch(() => "")
          : "";
        if (popup.isOpen()) {
          popup.setHTML(`<div class="popup">${text || coords}</div>`);
        }
      });
      m.on("moveend", () => {
        const c = m.getCenter();
        onViewChange(m.getZoom(), [c.lat, c.lng]);
      });

      created = m;
      setMap(m);
    };

    boot();
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      created?.remove();
      setMap(null);
      setStyleReady(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Swap the thematic source when layer/date changes — WITHOUT the flash.
  //
  // Instead of remove-then-add (which drops to bare basemap while the new
  // PMTiles header + tiles stream in), we alternate between two layer ids:
  // add the NEW one first, let it fade in over the old, then remove the old
  // a beat later. Scrubbing the date slider reads as a crossfade.
  //
  // Gated on styleReady (set once at the map's initial `load`) and applied
  // synchronously after that. Do NOT wait on `load`/`isStyleLoaded()` here:
  // `load` fires only once per map lifetime, and `isStyleLoaded()` reports
  // false whenever tiles are merely streaming — waiting on either silently
  // drops layer switches.
  const flipRef = useRef(0);
  const curThematic = useRef<string | null>(null);
  useEffect(() => {
    if (!map || !styleReady) return;
    // Capture the OLD slot's ids NOW — the ref mutates below.
    const oldIdx = flipRef.current;
    const oldLyr = `${THEMATIC_LAYER}-${oldIdx}`;
    const oldSrc = `${THEMATIC_SOURCE}-${oldIdx % 2}`;
    const removeOld = () => {
      if (map.getLayer(oldLyr)) map.removeLayer(oldLyr);
      if (map.getSource(oldSrc)) {
        const inUse = map.getStyle().layers?.some((l) => (l as any).source === oldSrc);
        if (!inUse) {
          try {
            map.removeSource(oldSrc);
          } catch {
            /* next swap cleans it */
          }
        }
      }
    };
    if (!external && !tilesUrl) {
      removeOld();
      curThematic.current = null;
      return;
    }
    const idx = ++flipRef.current;
    const src = `${THEMATIC_SOURCE}-${idx % 2}`;
    const lyr = `${THEMATIC_LAYER}-${idx}`;
    if (map.getSource(src)) {
      // Slot re-used before its removal timer fired — clear it now.
      map.getStyle().layers?.forEach((l) => {
        if ((l as any).source === src && map.getLayer(l.id)) map.removeLayer(l.id);
      });
      try {
        map.removeSource(src);
      } catch {
        /* already gone */
      }
    }
    if (external) {
      map.addSource(src, {
        type: "raster",
        tiles: external.tiles,
        tileSize: 256,
        maxzoom: external.maxzoom,
        attribution: external.attribution,
      });
      map.addLayer({
        id: lyr,
        type: "raster",
        source: src,
        paint: { "raster-opacity": external.opacity, "raster-fade-duration": 150 },
      });
    } else {
      map.addSource(src, {
        type: "raster",
        url: `pmtiles://${tilesUrl}`,
        tileSize: 256,
        maxzoom,
      });
      map.addLayer({
        id: lyr,
        type: "raster",
        source: src,
        paint: {
          "raster-opacity": rasterOpacity ?? 0.8,
          "raster-resampling": "linear",
          "raster-fade-duration": 150,
        },
      });
    }
    curThematic.current = lyr;
    // Old layer lingers briefly UNDER the incoming one, then goes — no gap.
    const t = window.setTimeout(removeOld, 400);
    return () => {
      window.clearTimeout(t);
      removeOld();
    };
  }, [map, styleReady, tilesUrl, maxzoom, external, rasterOpacity]);

  // Vector overlay (storm tracks): GeoJSON source + data-driven color,
  // rendered as three geometry-typed sublayers.
  useEffect(() => {
    if (!map || !styleReady) return;
    for (const id of VECTOR_LAYERS) if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource(VECTOR_SOURCE)) map.removeSource(VECTOR_SOURCE);
    if (!vector) return;
    // generateId gives every feature a numeric id → feature-state hover works.
    map.addSource(VECTOR_SOURCE, { type: "geojson", data: vector.url, generateId: true });
    const colorExpr: any = [
      "match",
      ["get", vector.colorProp],
      ...vector.colors.flatMap((c) => [String(c.value), c.color]),
      "#8899aa",
    ];
    // Three-tier interaction paint: selected (clicked) > hover (near) > base.
    const state = (sel: any, hov: any, base: any): any => [
      "case",
      ["boolean", ["feature-state", "selected"], false],
      sel,
      ["boolean", ["feature-state", "hover"], false],
      hov,
      base,
    ];
    const baseFill = vector.fillOpacity ?? 0.18;
    map.addLayer({
      id: "vec-fill",
      type: "fill",
      source: VECTOR_SOURCE,
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: {
        "fill-color": colorExpr,
        "fill-opacity": state(
          Math.min(baseFill + 0.3, 0.9),
          Math.min(baseFill + 0.15, 0.8),
          baseFill
        ),
      },
    });
    // Polygon outlines make alert/outlook/drought areas pop against the basemap.
    map.addLayer({
      id: "vec-outline",
      type: "line",
      source: VECTOR_SOURCE,
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "line-color": colorExpr, "line-width": state(3.5, 2.6, 1.6), "line-opacity": 0.9 },
    });
    map.addLayer({
      id: "vec-line",
      type: "line",
      source: VECTOR_SOURCE,
      filter: ["==", ["geometry-type"], "LineString"],
      paint: {
        "line-color": colorExpr,
        // NOTE: ["zoom"] may only appear in a TOP-LEVEL interpolate — the
        // feature-state cases go at the output positions, never around it.
        "line-width": [
          "interpolate", ["linear"], ["zoom"],
          3, state(3.5, 2.2, 1),
          6, state(5.5, 3.5, 2),
          9, state(8, 5.5, 3.5),
        ],
        "line-opacity": state(1, 1, 0.85),
      },
    });
    const r = vector.circleRadius ?? 8;
    map.addLayer({
      id: "vec-circle",
      type: "circle",
      source: VECTOR_SOURCE,
      filter: ["==", ["geometry-type"], "Point"],
      paint: {
        "circle-color": colorExpr,
        "circle-radius": state(r + 5, r + 3, r),
        "circle-stroke-color": state("#1c2321", "#ffffff", "#ffffff"),
        "circle-stroke-width": state(3, 2.5, r < 6 ? 1 : 2),
      },
    });
  }, [map, styleReady, vector]);

  // Animated live layers (radar, satellite): six timestamped frame sources;
  // the visible one follows animFrame (scrubbed or played).
  const ANIM_IDS = ["anim-f0", "anim-f1", "anim-f2", "anim-f3", "anim-f4", "anim-f5"];
  const framesKey = animFrames ? animFrames[0] : "";
  useEffect(() => {
    if (!map || !styleReady) return;
    ANIM_IDS.forEach((id) => {
      if (map.getLayer(id)) map.removeLayer(id);
      if (map.getSource(id)) map.removeSource(id);
    });
    const cur = curThematic.current;
    if (!animFrames) {
      if (cur && map.getLayer(cur)) map.setPaintProperty(cur, "raster-opacity", rasterOpacity ?? 0.8);
      return;
    }
    animFrames.forEach((tiles, i) => {
      map.addSource(ANIM_IDS[i], { type: "raster", tiles: [tiles], tileSize: 256 });
      map.addLayer({
        id: ANIM_IDS[i],
        type: "raster",
        source: ANIM_IDS[i],
        paint: { "raster-opacity": 0, "raster-fade-duration": 0 },
      });
    });
    if (cur && map.getLayer(cur)) map.setPaintProperty(cur, "raster-opacity", 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, styleReady, framesKey]);

  useEffect(() => {
    if (!map || !styleReady || !animFrames) return;
    ANIM_IDS.forEach((id, i) => {
      if (map.getLayer(id)) map.setPaintProperty(id, "raster-opacity", i === animFrame ? 0.85 : 0);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, styleReady, framesKey, animFrame]);

  // Viewport framing follows the active layer family.
  useEffect(() => {
    if (!map || !styleReady) return;
    const v = VIEWPORTS[viewport];
    map.setMaxBounds(v.bounds);
    map.setMinZoom(v.minZoom);
    if (map.getLayer("world-mask")) {
      map.setPaintProperty("world-mask", "fill-opacity", v.maskOpacity);
    }
  }, [map, styleReady, viewport]);

  // Apply/refresh user filters on the vector sublayers.
  useEffect(() => {
    if (!map || !styleReady) return;
    const geoms: [string, string][] = [
      ["vec-fill", "Polygon"],
      ["vec-outline", "Polygon"],
      ["vec-line", "LineString"],
      ["vec-circle", "Point"],
    ];
    for (const [id, g] of geoms) {
      if (!map.getLayer(id)) continue;
      const base: any = ["==", ["geometry-type"], g];
      map.setFilter(
        id,
        featureFilter?.length ? (["all", base, ...featureFilter] as any) : base
      );
    }
  }, [map, styleReady, vector, featureFilter]);

  // Chat-result pins: render markers and fly to fit them.
  useEffect(() => {
    if (!map || !pins) return;
    const popups: maplibregl.Popup[] = [];
    const markers = pins.map((p) => {
      const el = document.createElement("div");
      el.className = "pin";
      const popup = new maplibregl.Popup({ offset: 12, closeButton: true });
      popups.push(popup);
      // Click a pin → its label plus the full info card (place, layer value,
      // NWS mini-forecast) — same treatment as clicking the map.
      el.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        popup
          .setLngLat([p.lon, p.lat])
          .setHTML(`<div class="popup"><b>${p.label}</b><br/><em>…</em></div>`)
          .addTo(map);
        clickInfoRef.current?.({ lat: p.lat, lng: p.lon, feature: { pin: p.label } });
        const extra = sampleRef.current
          ? await sampleRef.current(p.lon, p.lat).catch(() => "")
          : "";
        if (extra && popup.isOpen()) {
          popup.setHTML(
            `<div class="popup"><b>${p.label}</b><div class="fc-sep"></div>${extra}</div>`
          );
        }
      });
      return new maplibregl.Marker({ element: el }).setLngLat([p.lon, p.lat]).addTo(map);
    });
    if (pins.length === 1) {
      map.flyTo({ center: [pins[0].lon, pins[0].lat], zoom: Math.max(map.getZoom(), 6) });
    } else if (pins.length > 1) {
      const lons = pins.map((p) => p.lon);
      const lats = pins.map((p) => p.lat);
      map.fitBounds(
        [
          [Math.min(...lons) - 0.5, Math.min(...lats) - 0.5],
          [Math.max(...lons) + 0.5, Math.max(...lats) + 0.5],
        ],
        { padding: 80, maxZoom: 8 }
      );
    }
    return () => {
      markers.forEach((mk) => mk.remove());
      popups.forEach((pp) => pp.remove());
    };
  }, [map, pins]);

  return <div ref={container} className="map" />;
}
