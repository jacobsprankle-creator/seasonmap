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
      m.on("click", async (e) => {
        // Vector layers: show the clicked feature's properties.
        const v = vectorRef.current;
        const present = VECTOR_LAYERS.filter((id) => m.getLayer(id));
        if (v && present.length) {
          const feats = m.queryRenderedFeatures(
            [
              [e.point.x - 4, e.point.y - 4],
              [e.point.x + 4, e.point.y + 4],
            ],
            { layers: present as unknown as string[] }
          );
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
        popup.setLngLat(e.lngLat).setHTML(`<div class="popup"><em>…</em></div>`).addTo(m);
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

  // Swap the thematic PMTiles source when layer/date changes; the slider
  // swaps URLs, adjacent dates stay warm in the browser cache.
  //
  // Gated on styleReady (set once at the map's initial `load`) and applied
  // synchronously after that. Do NOT wait on `load`/`isStyleLoaded()` here:
  // `load` fires only once per map lifetime, and `isStyleLoaded()` reports
  // false whenever tiles are merely streaming — waiting on either silently
  // drops layer switches.
  useEffect(() => {
    if (!map || !styleReady) return;
    if (map.getLayer(THEMATIC_LAYER)) map.removeLayer(THEMATIC_LAYER);
    if (map.getSource(THEMATIC_SOURCE)) map.removeSource(THEMATIC_SOURCE);
    if (external) {
      // Live third-party raster tiles (radar, satellite).
      map.addSource(THEMATIC_SOURCE, {
        type: "raster",
        tiles: external.tiles,
        tileSize: 256,
        maxzoom: external.maxzoom,
        attribution: external.attribution,
      });
      map.addLayer({
        id: THEMATIC_LAYER,
        type: "raster",
        source: THEMATIC_SOURCE,
        paint: { "raster-opacity": external.opacity },
      });
      return;
    }
    if (!tilesUrl) return;
    map.addSource(THEMATIC_SOURCE, {
      type: "raster",
      url: `pmtiles://${tilesUrl}`,
      tileSize: 256,
      maxzoom,
    });
    map.addLayer({
      id: THEMATIC_LAYER,
      type: "raster",
      source: THEMATIC_SOURCE,
      paint: { "raster-opacity": rasterOpacity ?? 0.8, "raster-resampling": "linear" },
    });
  }, [map, styleReady, tilesUrl, maxzoom, external, rasterOpacity]);

  // Vector overlay (storm tracks): GeoJSON source + data-driven color,
  // rendered as three geometry-typed sublayers.
  useEffect(() => {
    if (!map || !styleReady) return;
    for (const id of VECTOR_LAYERS) if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource(VECTOR_SOURCE)) map.removeSource(VECTOR_SOURCE);
    if (!vector) return;
    map.addSource(VECTOR_SOURCE, { type: "geojson", data: vector.url });
    const colorExpr: any = [
      "match",
      ["get", vector.colorProp],
      ...vector.colors.flatMap((c) => [String(c.value), c.color]),
      "#8899aa",
    ];
    map.addLayer({
      id: "vec-fill",
      type: "fill",
      source: VECTOR_SOURCE,
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": colorExpr, "fill-opacity": vector.fillOpacity ?? 0.18 },
    });
    // Polygon outlines make alert/outlook/drought areas pop against the basemap.
    map.addLayer({
      id: "vec-outline",
      type: "line",
      source: VECTOR_SOURCE,
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "line-color": colorExpr, "line-width": 1.6, "line-opacity": 0.9 },
    });
    map.addLayer({
      id: "vec-line",
      type: "line",
      source: VECTOR_SOURCE,
      filter: ["==", ["geometry-type"], "LineString"],
      paint: {
        "line-color": colorExpr,
        "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1, 6, 2, 9, 3.5],
        "line-opacity": 0.85,
      },
    });
    map.addLayer({
      id: "vec-circle",
      type: "circle",
      source: VECTOR_SOURCE,
      filter: ["==", ["geometry-type"], "Point"],
      paint: {
        "circle-color": colorExpr,
        "circle-radius": vector.circleRadius ?? 8,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": vector.circleRadius && vector.circleRadius < 6 ? 1 : 2,
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
    if (!animFrames) {
      if (map.getLayer(THEMATIC_LAYER)) map.setPaintProperty(THEMATIC_LAYER, "raster-opacity", rasterOpacity ?? 0.8);
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
    if (map.getLayer(THEMATIC_LAYER)) map.setPaintProperty(THEMATIC_LAYER, "raster-opacity", 0);
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
