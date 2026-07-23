import { useCallback, useEffect, useMemo, useState } from "react";
import { APP, DATA_BASE } from "./config";
import { ErrorBoundary } from "./ErrorBoundary";
import { MapView, type Pin } from "./map/MapView";
import { nwsPointInfo } from "./map/nws";
import { formatValue, sampleValue } from "./map/sampler";
import { readUrlState, writeUrlState } from "./state/url";
import { OverlayBar } from "./ui/OverlayBar";
import { baseLayerFor, LAYER_DEFS, type LayerMeta, OVERLAY_DEFS } from "./types";
import { ChatDrawer, type HighlightSpec } from "./chat/ChatDrawer";

// Session-scoped meta cache — revisiting a tab must not re-flash the UI.
const metaCache = new Map<string, LayerMeta>();

/** Default slider position: the present — the first date at/after today —
 * never the furthest-future frame. Falls back to the last date for
 * archive-style layers whose dates are all in the past. */
function defaultDate(m: LayerMeta): string | null {
  if (!m.dates.length) return m.latest;
  const today = new Date().toISOString().slice(0, 10);
  return m.dates.find((d) => d.slice(0, 10) >= today) ?? m.dates[m.dates.length - 1];
}
import { DateSlider } from "./ui/DateSlider";
import { FilterPanel } from "./ui/FilterPanel";
import { LayerSwitcher } from "./ui/LayerSwitcher";
import { Legend } from "./ui/Legend";
import { StatusBadge } from "./ui/StatusBadge";

export default function App() {
  const initial = useMemo(readUrlState, []);
  const [layerId, setLayerId] = useState<string>(() => {
    const requested = initial.layer;
    if (requested && baseLayerFor(requested)?.available) return requested;
    // Boot to the first layer's default VARIANT — the bare base id of a
    // variant group (e.g. "conditions") has no meta of its own.
    const first = LAYER_DEFS.find((l) => l.available)!;
    const dflt = first.variants?.find((v) => v.isDefault) ?? first.variants?.[0];
    return dflt ? dflt.id : first.id;
  });
  const baseLayer = baseLayerFor(layerId);
  const [overlayIds, setOverlayIds] = useState<string[]>(initial.overlays ?? []);
  const toggleOverlay = useCallback(
    (id: string) =>
      setOverlayIds((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id])),
    []
  );
  const activeOverlays = useMemo(
    () => OVERLAY_DEFS.filter((o) => overlayIds.includes(o.id)),
    [overlayIds]
  );
  const [meta, setMeta] = useState<LayerMeta | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [date, setDate] = useState<string | null>(initial.date ?? null);
  const [view, setView] = useState<{ zoom?: number; center?: [number, number] }>({
    zoom: initial.zoom,
    center: initial.center,
  });
  const [pins, setPins] = useState<Pin[]>([]);
  const [featureFilter, setFeatureFilter] = useState<unknown[][] | null>(null);
  const [filterDesc, setFilterDesc] = useState<string | null>(null);
  const [lastClick, setLastClick] = useState<{
    lat: number;
    lng: number;
    feature: Record<string, unknown> | null;
  } | null>(null);
  const [chatHighlight, setChatHighlight] = useState<unknown[] | null>(null);

  const applyHighlights = useCallback((hs: HighlightSpec[]) => {
    if (!hs.length) return;
    setLayerId(hs[0].layer); // jump the map to the referenced layer
    const clause = (w: HighlightSpec["where"][number]): unknown[] => {
      if (w.equals !== undefined)
        return ["==", ["to-string", ["get", w.prop]], String(w.equals)];
      const parts: unknown[] = ["all"];
      if (w.min !== undefined) parts.push([">=", ["to-number", ["get", w.prop]], w.min]);
      if (w.max !== undefined) parts.push(["<=", ["to-number", ["get", w.prop]], w.max]);
      return parts;
    };
    setChatHighlight([
      "any",
      ...hs.map((h) => ["all", ...h.where.map(clause)]),
    ]);
  }, []);

  const activeVariant = baseLayer?.variants?.find((v) => v.id === layerId);
  const external = activeVariant?.external ?? baseLayer?.external ?? null;
  const externalVec = activeVariant?.externalVector ?? baseLayer?.externalVector ?? null;
  const [animPlaying, setAnimPlaying] = useState(false);
  const [animFrame, setAnimFrame] = useState(5); // 0=oldest … 5=newest
  useEffect(() => {
    setAnimPlaying(false);
    setAnimFrame(5);
    setPins([]); // stale pins shouldn't haunt the next layer
  }, [layerId]);
  useEffect(() => {
    if (!animPlaying) return;
    const t = setInterval(() => setAnimFrame((f) => (f + 1) % 6), 650);
    return () => clearInterval(t);
  }, [animPlaying]);

  // Frame tile-URL sets for animatable live layers.
  const animFrames = useMemo(() => {
    if (layerId === "radar") {
      return ["-m50m", "-m40m", "-m30m", "-m20m", "-m10m", ""].map(
        (s) =>
          `https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913${s}/{z}/{x}/{y}.png`
      );
    }
    if (layerId === "satellite") {
      // GIBS GeoColor: 10-min granules, ~40 min publish latency.
      const base = Date.now() - 40 * 60000;
      return [5, 4, 3, 2, 1, 0].map((back) => {
        const t = new Date(Math.floor((base - back * 10 * 60000) / 600000) * 600000);
        const ts = t.toISOString().slice(0, 19) + "Z";
        return `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-East_ABI_GeoColor/default/${ts}/GoogleMapsCompatible_Level7/{z}/{y}/{x}.jpg`;
      });
    }
    return null;
  }, [layerId]);

  useEffect(() => {
    let cancelled = false;
    setMetaError(null);
    if (external || externalVec) {
      setMeta(null);
      return; // live third-party feeds carry no pipeline meta
    }
    // Stale-while-revalidate: a revisited layer renders instantly from cache
    // (no bottombar collapse, no raster teardown) while a background fetch
    // freshens it.
    const cached = metaCache.get(layerId);
    if (cached) {
      setMeta(cached);
      setDate((d) => (d && cached.dates.includes(d) ? d : defaultDate(cached)));
    } else {
      setMeta(null);
    }
    fetch(`${DATA_BASE}/meta/${layerId}/latest.json`)
      .then((r) => {
        if (!r.ok) throw new Error(`meta HTTP ${r.status}`);
        return r.json() as Promise<LayerMeta>;
      })
      .then((m) => {
        metaCache.set(layerId, m);
        if (cancelled) return;
        setMeta(m);
        setDate((d) => (d && m.dates.includes(d) ? d : defaultDate(m)));
      })
      .catch((e) => !cancelled && !cached && setMetaError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [layerId, external, externalVec]);

  useEffect(() => {
    if (date) writeUrlState({ layer: layerId, date, overlays: overlayIds, ...view });
  }, [layerId, date, view, overlayIds]);

  const onViewChange = useCallback(
    (zoom: number, center: [number, number]) => setView({ zoom, center }),
    []
  );

  const isVector = meta?.type === "vector";
  const hourly = !!(meta as any)?.hourly && (meta?.dates.length ?? 0) > 12;
  // Streamed layers publish run-scoped tiles: the template carries a {run}
  // token resolved from meta.run (date-keyed layers pass through unchanged).
  const resolveTiles = useCallback(
    (m: NonNullable<typeof meta>, d: string) =>
      `${DATA_BASE}/${m.tiles.replace("{run}", (m as any).run ?? "").replace("{date}", d)}`,
    []
  );
  const frameUrls = useMemo(
    () =>
      hourly && meta
        ? meta.dates.map((d) => resolveTiles(meta, d))
        : null,
    [hourly, meta, resolveTiles]
  );
  const frameIndex = hourly && meta && date ? Math.max(0, meta.dates.indexOf(date)) : 0;
  const [framePlaying, setFramePlaying] = useState(false);
  useEffect(() => setFramePlaying(false), [layerId]);
  useEffect(() => {
    if (!framePlaying || !hourly || !meta) return;
    const t = setInterval(() => {
      setDate((d) => {
        const i = d ? meta.dates.indexOf(d) : 0;
        return meta.dates[(i + 1) % meta.dates.length];
      });
    }, 320);
    return () => clearInterval(t);
  }, [framePlaying, hourly, meta]);
  const tilesUrl =
    meta && date && !isVector && !hourly
      ? resolveTiles(meta, date)
      : null;
  const vector = externalVec
    ? {
        url: externalVec.url,
        geometry: "fill",
        colorProp: externalVec.colorProp,
        hover: externalVec.hover,
        colors: externalVec.colors.map((i) => ({ value: i.value, color: i.color })),
        fillOpacity: externalVec.fillOpacity,
      }
    : meta && date && isVector && meta.style
      ? {
          url: `${DATA_BASE}/${meta.data.replace("{date}", date)}`,
          geometry: meta.style.geometry,
          colorProp: meta.style.color_property,
          hover: meta.style.hover,
          groupProp: meta.style.group,
          colors: (meta.legend.items ?? []).map((i) => ({ value: i.value, color: i.color })),
          fillOpacity: meta.style.fill_opacity,
          circleRadius: meta.style.circle_radius,
        }
      : null;

  const sample = useCallback(
    async (lng: number, lat: number) => {
      const parts: string[] = [];
      const info = await nwsPointInfo(lat, lng);
      if (info.place) parts.push(`<div class="fc-place">Near ${info.place}</div>`);
      if (meta && date && !isVector) {
        try {
          const cogUrl = `${DATA_BASE}/${meta.data.replace("{date}", date)}`;
          parts.push(
            `<div class="fc-value">${formatValue(await sampleValue(cogUrl, meta, lng, lat), meta)}</div>`
          );
        } catch {
          /* layer value optional when NWS is available */
        }
      }
      if (info.periods.length) {
        parts.push(
          `<div class="fc-card">` +
            info.periods
              .map(
                (per) =>
                  `<div class="fc-row"><span class="fc-name">${per.name}</span><span>${per.short} · <b>${per.temp}</b></span></div>`
              )
              .join("") +
            `</div>`
        );
      }
      return parts.join("") || "no data";
    },
    [meta, date, isVector]
  );

  return (
    <div className="app">
      <OverlayBar active={overlayIds} onToggle={toggleOverlay} />
      <ErrorBoundary label="Map">
        <MapView
          tilesUrl={tilesUrl}
          vector={vector}
          external={external}
          featureFilter={
            chatHighlight
              ? [...(featureFilter ?? []), chatHighlight as unknown[]]
              : featureFilter
          }
          viewport={baseLayer?.viewport ?? "conus"}
          animFrames={animFrames}
          animFrame={animFrame}
          rasterOpacity={(meta as any)?.opacity}
          maxzoom={meta?.maxzoom ?? 7}
          frameUrls={frameUrls}
          frameIndex={frameIndex}
          initialView={initial}
          onViewChange={onViewChange}
          sample={sample}
          pins={pins}
          overlays={activeOverlays}
          onClickInfo={setLastClick}
        />
      </ErrorBoundary>

      <header className="topbar">
        <LayerSwitcher
          layers={LAYER_DEFS}
          active={baseLayer?.id ?? layerId}
          onChange={(id) => {
            const def = LAYER_DEFS.find((l) => l.id === id);
            const dflt = def?.variants?.find((v) => v.isDefault) ?? def?.variants?.[0];
            setLayerId(dflt ? dflt.id : id);
          }}
          brand={
            <div className="brand">
              <span className="brand-name">{APP.name}</span>
              <span className="brand-tag">{APP.tagline}</span>
            </div>
          }
          variantSlot={
            baseLayer?.variants ? (
              <div className="variant-toggle" role="tablist" aria-label="Sub-layer">
                {baseLayer.variants.map((v) => (
                  <button
                    key={v.id}
                    role="tab"
                    aria-selected={v.id === layerId}
                    className={v.id === layerId ? "chip chip-active" : "chip"}
                    onClick={() => setLayerId(v.id)}
                  >
                    {v.label}
                  </button>
                ))}
              </div>
            ) : undefined
          }
        />
      </header>

      {chatHighlight && (
        <button className="highlight-chip" onClick={() => setChatHighlight(null)}>
          Showing chat highlights · tap to clear ✕
        </button>
      )}

      {meta && meta.status.state !== "ok" && <StatusBadge status={meta.status} />}
      {metaError && (
        <StatusBadge
          status={{ state: "error", message: `No data published for “${layerId}” (${metaError})`, generated_at: "" }}
        />
      )}

      <footer className="bottombar">
        {meta && isVector && (
          <FilterPanel
            meta={meta}
            onChange={(f, d) => {
              setFeatureFilter(f);
              setFilterDesc(d);
            }}
          />
        )}
        {meta && date && (
          <DateSlider
            dates={meta.dates}
            value={date}
            onChange={(d) => { setFramePlaying(false); setDate(d); }}
            playing={hourly ? framePlaying : undefined}
            onPlayToggle={hourly ? () => setFramePlaying((p) => !p) : undefined}
          />
        )}
        {animFrames && (
          <div className="radar-control">
            <button
              className="radar-play"
              onClick={() => setAnimPlaying((p) => !p)}
              aria-label={animPlaying ? "Pause" : "Play"}
            >
              {animPlaying ? "⏸" : "▶"}
            </button>
            <input
              type="range"
              min={0}
              max={5}
              step={1}
              value={animFrame}
              onChange={(e) => {
                setAnimPlaying(false);
                setAnimFrame(Number(e.target.value));
              }}
              aria-label="Animation frame"
            />
            <output>{["−50 min", "−40 min", "−30 min", "−20 min", "−10 min", "latest"][animFrame]}</output>
          </div>
        )}
        {meta && <Legend legend={meta.legend} />}
        {externalVec && (
          <Legend legend={{ type: "categorical", items: externalVec.colors } as LayerMeta["legend"]} />
        )}
        {(meta || external || externalVec) && (
          <div className="meta-caption">
            {external ? external.caption : externalVec ? externalVec.caption : meta!.description}
            {(meta as any)?.model_run
              ? ` · init ${(meta as any).model_run} (${new Date((meta as any).model_run.replace("Z", ":00Z")).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })} local)`
              : ""}
            {!external && !externalVec && meta!.dates.length < 2 ? " · updated nightly" : ""}
          </div>
        )}
      </footer>

      <ErrorBoundary label="Assistant">
        <ChatDrawer
          onPins={setPins}
          onHighlights={applyHighlights}
          onSwitchLayer={(id) => {
            if (baseLayerFor(id)) setLayerId(id);
          }}
          context={{
            layer: layerId,
            layer_label: baseLayer?.label ?? layerId,
            date,
            filters: filterDesc,
            view: view.center ? { lat: view.center[0], lng: view.center[1], zoom: view.zoom } : null,
            click: lastClick,
          }}
        />
      </ErrorBoundary>
    </div>
  );
}
