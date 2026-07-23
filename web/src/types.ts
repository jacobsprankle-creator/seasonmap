export interface LegendStop {
  value: number;
  color: string;
}

export interface LayerMeta {
  layer: string;
  description: string;
  updated_at: string;
  dates: string[];
  latest: string | null;
  tiles: string; // "tiles/{layer}/{date}.pmtiles" template
  data: string;
  minzoom: number;
  maxzoom: number;
  units: string;
  type?: "vector";
  /** Streamed model layers: run-scoped tiles + the run picker's data. */
  hourly?: boolean;
  model_run?: string;
  run?: string;
  runs?: string[];
  runs_dates?: Record<string, string[]>;
  query?: string;
  style?: {
    geometry: string;
    color_property: string;
    hover: string[];
    group?: string;
    fill_opacity?: number;
    circle_radius?: number;
  };
  filters?: { year_range: [number, number]; category_property: string };
  value_format?: "number" | "probability" | "doy_date" | "snow_state" | "feature" | "foliage_stage";
  grid?: {
    width: number;
    height: number;
    transform: [number, number, number, number, number, number];
    crs: string;
    nodata: number;
  };
  legend: {
    type: "gradient" | "categorical";
    colormap?: string;
    stepped?: boolean;
    vmin?: number;
    vmax?: number;
    units?: string;
    stops?: LegendStop[];
    labels?: { value: number; label: string }[];
    items?: { value: string | number; color: string; label: string }[];
  };
  stats: Record<string, number | null>;
  status: {
    state: "ok" | "degraded" | "error";
    message: string | null;
    generated_at: string;
  };
}

export interface LayerVariant {
  id: string;
  label: string;
  isDefault?: boolean;
  group?: string; // ignored on variants; present only via registry generation
  /** Variant backed by live third-party tiles instead of pipeline meta. */
  external?: ExternalTiles;
  externalVector?: ExternalVector;
}

export interface ExternalTiles {
  tiles: string[];
  attribution: string;
  caption: string;
  maxzoom: number;
  opacity: number;
}

/** Live third-party GeoJSON (CORS-open feeds like NWS alerts, SPC outlooks). */
export interface ExternalVector {
  url: string;
  colorProp: string;
  colors: { value: string; color: string; label: string }[];
  hover: string[];
  caption: string;
  fillOpacity: number;
}

export interface LayerDef {
  id: string;
  label: string;
  phase: number;
  available: boolean;
  /** Threshold/sub-product toggle: variant ids are real published layers. */
  variants?: LayerVariant[];
  /** Live third-party raster tiles — no pipeline meta, always current. */
  external?: ExternalTiles;
  externalVector?: ExternalVector;
  /** Viewport framing: tight CONUS (default) or relaxed hurricane basin. */
  viewport?: "conus" | "basin";
  /** Nav grouping tab. */
  group: string;
}

const SPC_COLORS = [
  { value: "TSTM", color: "#C1E9C1", label: "T-storm" },
  { value: "MRGL", color: "#66A366", label: "Marginal" },
  { value: "SLGT", color: "#FFE066", label: "Slight" },
  { value: "ENH", color: "#FFA366", label: "Enhanced" },
  { value: "MDT", color: "#E06666", label: "Moderate" },
  { value: "HIGH", color: "#EE99EE", label: "High" },
];

const spcDay = (n: number, isDefault = false) => ({
  id: `outlook_day${n}`,
  label: `Day ${n}`,
  isDefault,
  externalVector: {
    url: `https://www.spc.noaa.gov/products/outlook/day${n}otlk_cat.lyr.geojson`,
    colorProp: "LABEL",
    colors: SPC_COLORS,
    hover: ["LABEL2", "VALID", "EXPIRE"],
    caption: `SPC Day ${n} categorical severe weather outlook (live) · click a risk area for details`,
    fillOpacity: 0.45,
  },
});

/** Overlays — sparse live layers that stack on TOP of whatever base layer
 *  is active. The design rule: FILLS FIGHT, SPARSE STACKS. Full-bleed raster
 *  fills (temps, foliage, drought fill) stay single-select bases because two
 *  opaque fills are unreadable — but anything line/point/outline-shaped
 *  stacks indefinitely: radar echoes, warning polygons, storm tracks, gauge
 *  points, outlook outlines. Any number can be on at once; the active set
 *  rides the URL (?ov=radar,alerts,rivers). New overlays are registry rows —
 *  GLM lightning and MRMS slot in here when NODD lands. */
export interface OverlayDef {
  id: string;
  label: string;
  /** rail swatch color */
  swatch: string;
  source:
    | { kind: "tiles"; tiles: string[]; maxzoom?: number; opacity?: number; refreshMs?: number }
    | { kind: "geojson"; url: string; refreshMs?: number }
    | { kind: "layerdata"; layers: string[] };
  style?: {
    colorProp?: string;
    colors?: { value: string; color: string }[];
    fallback?: string;
    /** polygons ignore colorProp when set (e.g. neutral NHC cones) */
    polygonAccent?: string;
    fillOpacity?: number;
    lineWidth?: number;
    lineOpacity?: number;
    circleRadius?: number;
    dashOutline?: boolean;
  };
}

export const OVERLAY_DEFS: OverlayDef[] = [
  {
    id: "radar",
    label: "Radar",
    swatch: "#43a047",
    source: {
      kind: "tiles",
      tiles: [
        "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png",
      ],
      maxzoom: 12,
      opacity: 0.78,
      refreshMs: 300_000, // NEXRAD composite refreshes ~5 min
    },
  },
  {
    id: "alerts",
    label: "Alerts",
    swatch: "#f57c00",
    source: {
      kind: "geojson",
      url: "https://api.weather.gov/alerts/active?status=actual&message_type=alert",
      refreshMs: 180_000,
    },
    style: {
      colorProp: "severity",
      colors: [
        { value: "Extreme", color: "#b71c1c" },
        { value: "Severe", color: "#f57c00" },
        { value: "Moderate", color: "#fbc02d" },
        { value: "Minor", color: "#78909c" },
      ],
      fillOpacity: 0.1,
      lineWidth: 1.5,
      lineOpacity: 0.85,
    },
  },
  {
    id: "storms",
    label: "Active storms",
    swatch: "#d02c2c",
    source: { kind: "layerdata", layers: ["hurricanes_active"] },
    style: {
      colorProp: "cat",
      colors: [
        { value: "TD", color: "#9aa5b1" },
        { value: "TS", color: "#5ba7d1" },
        { value: "1", color: "#f2d15c" },
        { value: "2", color: "#f0a13c" },
        { value: "3", color: "#e8642d" },
        { value: "4", color: "#d02c2c" },
        { value: "5", color: "#8e24aa" },
      ],
      polygonAccent: "#5c6bc0",
      fillOpacity: 0.1,
      lineWidth: 2.4,
      circleRadius: 4.5,
      dashOutline: true,
    },
  },
  {
    id: "outlook",
    label: "Severe outlook",
    swatch: "#e8642d",
    source: {
      kind: "geojson",
      url: "https://www.spc.noaa.gov/products/outlook/day1otlk_cat.lyr.geojson",
      refreshMs: 1_800_000,
    },
    style: {
      colorProp: "LABEL",
      colors: SPC_COLORS,
      fillOpacity: 0.16,
      lineWidth: 1.4,
    },
  },
  {
    id: "rivers",
    label: "River gauges",
    swatch: "#3288bd",
    source: { kind: "layerdata", layers: ["rivers"] },
    style: {
      colorProp: "temp_class",
      colors: [
        { value: "lt50", color: "#5e4fa2" },
        { value: "50s", color: "#3288bd" },
        { value: "60s", color: "#66c2a5" },
        { value: "70s", color: "#fee08b" },
        { value: "80plus", color: "#d53e4f" },
        { value: "flow_only", color: "#9aa5b1" },
      ],
      circleRadius: 3.5,
    },
  },
  {
    id: "tornado",
    label: "Tornado tracks",
    swatch: "#8e24aa",
    source: { kind: "layerdata", layers: ["tornadoes_violent", "tornadoes_strong"] },
    style: {
      colorProp: "ef",
      colors: [
        { value: "2", color: "#f0a13c" },
        { value: "3", color: "#e8642d" },
        { value: "4", color: "#d02c2c" },
        { value: "5", color: "#8e24aa" },
      ],
      fallback: "#f0a13c",
      lineWidth: 2,
    },
  },
  {
    id: "drought",
    label: "Drought",
    swatch: "#FFAA00",
    source: { kind: "layerdata", layers: ["drought"] },
    style: {
      colorProp: "dm",
      colors: [
        { value: "D0", color: "#FFFF54" },
        { value: "D1", color: "#FCD37F" },
        { value: "D2", color: "#FFAA00" },
        { value: "D3", color: "#E60000" },
        { value: "D4", color: "#730000" },
      ],
      fillOpacity: 0.15,
      lineWidth: 1.2,
    },
  },
];

/** Thematic layer registry — one active layer at a time in v1. */
export const LAYER_DEFS: LayerDef[] = [
  {
    id: "conditions",
    group: "Now",
    label: "Current Conditions",
    phase: 0,
    available: true,
    variants: [
      {
        id: "radar",
        label: "Radar",
        isDefault: true,
        external: {
          tiles: [
            "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png",
          ],
          attribution: "NEXRAD composite © Iowa Environmental Mesonet",
          caption:
            "Live NEXRAD composite radar (IEM), refreshes ~every 5 minutes · click anywhere for the NWS forecast",
          maxzoom: 12,
          opacity: 0.85,
        },
      },
      {
        id: "satellite",
        label: "Satellite",
        external: {
          tiles: [
            "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-East_ABI_GeoColor/default/default/GoogleMapsCompatible_Level7/{z}/{y}/{x}.jpg",
          ],
          attribution: "GOES-East GeoColor · NASA GIBS",
          caption:
            "Latest GOES-East GeoColor satellite (NASA GIBS) · click anywhere for the NWS forecast",
          maxzoom: 7,
          opacity: 1.0,
        },
      },
      { id: "conditions_temp", label: "Temp" },
      { id: "conditions_humidity", label: "Humidity" },
      { id: "conditions_dewpoint", label: "Dew point" },
      { id: "conditions_wind", label: "Wind" },
    ],
  },
  {
    id: "nbm",
    group: "Models",
    label: "NBM (NWS Blend)",
    phase: 6,
    available: true,
    variants: [
      { id: "nbm_tmax", label: "Temp", isDefault: true },
      { id: "nbm_precip3", label: "Precip step" },
    ],
  },
  ...[
    ["gfs", "GFS", true],
    ["euro", "Euro (ECMWF)", true],
    ["hrrr", "HRRR", false],
    ["ukmet", "UKMET", true],
    ["icon", "ICON", true],
    ["gem", "GEM", true],
  ].map(([id, label, hasUpper]): LayerDef => ({
    id: id as string,
    group: "Models",
    label: label as string,
    phase: 6,
    available: true,
    variants: [
      { id: `${id}_sfc`, label: "Sfc", isDefault: true },
      { id: `${id}_tmax`, label: "Temp" },
      { id: `${id}_precip3`, label: "Precip step" },
      { id: `${id}_precip24`, label: "Precip 24h" },
      { id: `${id}_precip`, label: "Precip Total" },
      { id: `${id}_snow`, label: "Snow" },
      { id: `${id}_mslp`, label: "MSLP" },
      ...(hasUpper
        ? [
            { id: `${id}_z500`, label: "500mb" },
            { id: `${id}_w250`, label: "250mb jet" },
          ]
        : []),
      { id: `${id}_gusts`, label: "Gusts" },
      { id: `${id}_cape`, label: "CAPE" },
    ],
  })),
  {
    id: "alerts",
    group: "Now",
    label: "Alerts (live)",
    phase: 0,
    available: true,
    externalVector: {
      url: "https://api.weather.gov/alerts/active?status=actual&message_type=alert",
      colorProp: "severity",
      colors: [
        { value: "Extreme", color: "#b71c1c", label: "Extreme" },
        { value: "Severe", color: "#f57c00", label: "Severe" },
        { value: "Moderate", color: "#fbc02d", label: "Moderate" },
        { value: "Minor", color: "#78909c", label: "Minor" },
      ],
      hover: ["event", "severity", "areaDesc"],
      caption: "Live NWS watches, warnings & advisories · click a shape for details",
      fillOpacity: 0.55,
    },
  },
  {
    id: "outlooks",
    group: "Now",
    label: "Severe Outlook",
    phase: 0,
    available: true,
    variants: [spcDay(1, true), spcDay(2), spcDay(3)],
  },
  {
    id: "air",
    group: "Now",
    label: "Air Quality",
    phase: 6,
    available: true,
    variants: [
      { id: "air_aqi", label: "AQI", isDefault: true },
      { id: "air_pm25", label: "PM2.5" },
      { id: "air_smoke", label: "Smoke" },
    ],
  },
  {
    id: "frost_date",
    group: "Seasons",
    label: "First Frost/Freeze",
    phase: 1,
    available: true,
    variants: [
      { id: "frost_date_36", label: "Frost 36°F" },
      { id: "frost_date", group: "Seasons", label: "Freeze 32°F", isDefault: true },
      { id: "frost_date_28", label: "Hard 28°F" },
    ],
  },
  { id: "frost", group: "Seasons", label: "Freeze Probability", phase: 1, available: true },
  { id: "snowline", group: "Seasons", label: "Snow Line", phase: 3, available: true },
  {
    id: "water_temp",
    group: "Water",
    label: "Water Temp",
    phase: 6,
    available: true,
    variants: [
      { id: "water_temp", group: "Water", label: "Oceans & Lakes", isDefault: true },
      { id: "rivers", label: "River gauges" },
      { id: "waves", label: "Waves" },
    ],
  },
  { id: "drought", group: "Water", label: "Drought", phase: 6, available: true },
  {
    id: "hurricanes",
    group: "Storms",
    label: "Hurricane Tracks",
    phase: 6,
    available: true,
    viewport: "basin",
    variants: [
      { id: "hurricanes_majors", label: "Majors (Cat 3+)", isDefault: true },
      { id: "hurricanes_modern", label: "All since 2000" },
      { id: "hurricanes_active", label: "Active now" },
    ],
  },
  {
    id: "tornadoes",
    group: "Storms",
    label: "Tornado Tracks",
    phase: 6,
    available: true,
    variants: [
      { id: "tornadoes_violent", label: "EF4–EF5", isDefault: true },
      { id: "tornadoes_strong", label: "EF2–EF3 (1980+)" },
      { id: "tornadoes_weak", label: "EF0–EF1 (2000+)" },
    ],
  },
  { id: "foliage", group: "Seasons", label: "Fall Foliage", phase: 5, available: true },
  {
    id: "leafout",
    group: "Seasons",
    label: "Leaf-Out & Bloom",
    phase: 4,
    available: true,
    variants: [
      { id: "leafout", group: "Seasons", label: "First leaf", isDefault: true },
      { id: "leafout_bloom", label: "First bloom" },
    ],
  },
  { id: "wildflower", group: "Seasons", label: "Wildflowers", phase: 4, available: false },
];

export function baseLayerFor(effectiveId: string): LayerDef | undefined {
  return LAYER_DEFS.find(
    (l) => l.id === effectiveId || l.variants?.some((v) => v.id === effectiveId)
  );
}
