"""Resort catalog, constants, and the typed records loaded from data/*.json.

CATALOG holds rough coordinates; calibrate.py snaps them to Open-Meteo's 90 m DEM (the published
lift elevation is kept, since the DEM smooths peaks), picks a gauge and the models with data
there, and writes data/resorts.json + data/calibration.json.
"""
import json, os
from dataclasses import asdict, dataclass, field, fields

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
FT, IN, KMH_MPH = 0.3048, 25.4, 0.621371
ELEVS = ("base", "mid", "summit")

# Deterministic models blended for the central forecast; each resort keeps those with data there.
BASE_MODELS = ["ecmwf_ifs", "ecmwf_ifs025", "ecmwf_aifs025_single", "gfs_seamless",
               "ncep_nbm_conus", "gem_seamless", "icon_seamless"]
EXTRA_MODELS = ["gem_hrdps_continental"]  # 2.5 km Canadian; BC, Canadian Rockies + northern US
PROFILE_MODELS = ["ecmwf_ifs025", "gfs_seamless", "gem_seamless", "icon_seamless"]  # pressure levels
LEVELS = [1000, 925, 850, 800, 750, 700, 650, 600, 550, 500]
# Ensemble -> deterministic sibling whose calibration factor applies to its members.
ENSEMBLES = {"ecmwf_ifs025": "ecmwf_ifs025", "ecmwf_aifs025": "ecmwf_aifs025_single",
             "gfs05": "gfs_seamless", "gem_global": "gem_seamless", "icon_seamless": "icon_seamless"}
SEASONS = [("2024-11-01", "2025-04-30"), ("2025-11-01", "2026-04-30")]  # calibration winters
EXT_LEAD_DAYS = 3.5  # forecasts further out than this use the lead 4-7 day weights
# NWS asks for a contact in the User-Agent; set SKISITE_CONTACT (e.g. an email) when hosted.
USER_AGENT = f"skisite-personal ({os.environ.get('SKISITE_CONTACT', 'local use')})"
def slug(region):
    """Region id for the CLI and CI jobs (one parallel job per region): "Coastal BC" -> "coastal-bc"."""
    return region.lower().replace(" ", "-").replace(".", "")


NON_US = {"Coastal BC", "Interior BC", "Canadian Rockies"}  # no NWS products / SNOTEL
BC_REGIONS = {"Coastal BC", "Interior BC"}  # gauge search: BC snow pillows (ASWS)
SNOTEL_STATES = {"California": ["CA", "NV"], "Pacific Northwest": ["WA", "OR"], "Utah": ["UT"],
                 "Northern Rockies": ["WY", "MT", "ID"], "Colorado": ["CO"]}

PT, MT = "America/Los_Angeles", "America/Denver"
VAN, EDM = "America/Vancouver", "America/Edmonton"
# key: (name, region, tz, (base lat, lon, ft, label), (summit lat, lon, ft, label)); rough coordinates
CATALOG = {
    "palisades": ("Palisades Tahoe", "California", PT, (39.1969, -120.2354, 6200, "Village"), (39.1985, -120.2936, 9006, "Granite Chief")),
    "alpine": ("Alpine Meadows", "California", PT, (39.1645, -120.2386, 6835, "Base lodge"), (39.1489, -120.2476, 8637, "Ward Peak")),
    "kirkwood": ("Kirkwood", "California", PT, (38.6847, -120.0654, 7800, "Base"), (38.6650, -120.0610, 9876, "Thimble Peak ridge")),
    "mammoth": ("Mammoth Mountain", "California", PT, (37.6510, -119.0270, 8900, "Main Lodge"), (37.6280, -119.0325, 11053, "Summit")),
    "bachelor": ("Mt. Bachelor", "Pacific Northwest", PT, (43.9922, -121.6786, 6300, "West Village"), (43.9790, -121.6886, 9065, "Summit")),
    "alta": ("Alta", "Utah", MT, (40.5885, -111.6380, 8530, "Wildcat base"), (40.5770, -111.6300, 10550, "Upper mountain")),
    "snowbird": ("Snowbird", "Utah", MT, (40.5812, -111.6555, 7760, "Base"), (40.5605, -111.6455, 11000, "Hidden Peak")),
    "solitude": ("Solitude", "Utah", MT, (40.6197, -111.5917, 7988, "Base"), (40.6020, -111.5790, 10488, "Summit")),
    "snowbasin": ("Snowbasin", "Utah", MT, (41.2163, -111.8570, 6400, "Base"), (41.2000, -111.8800, 9350, "Needles")),
    "jackson": ("Jackson Hole", "Northern Rockies", MT, (43.5877, -110.8279, 6311, "Teton Village"), (43.5970, -110.8710, 10450, "Rendezvous")),
    "bigsky": ("Big Sky", "Northern Rockies", MT, (45.2857, -111.4012, 7500, "Mountain Village"), (45.2784, -111.4502, 11166, "Lone Peak")),
    "abasin": ("Arapahoe Basin", "Colorado", MT, (39.6420, -105.8715, 10780, "Base"), (39.6300, -105.8650, 13050, "Summit")),
    "copper": ("Copper Mountain", "Colorado", MT, (39.5018, -106.1511, 9712, "Base"), (39.4720, -106.1660, 12313, "Tucker Mtn")),
    "steamboat": ("Steamboat", "Colorado", MT, (40.4572, -106.8045, 6900, "Base"), (40.4563, -106.7485, 10568, "Mt. Werner")),
    "telluride": ("Telluride", "Colorado", MT, (37.9369, -107.8467, 8725, "Mountain Village"), (37.9105, -107.8380, 12570, "Gold Hill")),
    "crestedbutte": ("Crested Butte", "Colorado", MT, (38.8991, -106.9650, 9375, "Base"), (38.8877, -106.9440, 12162, "North Face / High Lift")),
    "whistler": ("Whistler", "Coastal BC", VAN, (50.1150, -122.9548, 2214, "Village"), (50.0610, -122.9480, 7160, "Whistler Peak")),
    "blackcomb": ("Blackcomb", "Coastal BC", VAN, (50.1155, -122.9480, 2214, "Village"), (50.0950, -122.8880, 7494, "Glacier / 7th Heaven")),
    "revelstoke": ("Revelstoke", "Interior BC", VAN, (50.9580, -118.1636, 1680, "Base"), (50.9390, -118.2310, 7300, "Summit")),
    "kickinghorse": ("Kicking Horse", "Interior BC", EDM, (51.2975, -117.0480, 3900, "Base"), (51.2830, -117.0920, 8033, "Terminator Peak")),
    "lakelouise": ("Lake Louise", "Canadian Rockies", EDM, (51.4420, -116.1620, 5400, "Base"), (51.4615, -116.1290, 8650, "Top of the World")),
}
# Hand-verified mid points and gauges for the original four (kept as-is by locate), and gauges for
# regions without a searchable station catalog (Alberta: id "station:precip-TSID").
MANUAL = {
    "palisades": dict(mid=(39.1899, -120.2658, 8000, "High Camp"),
                      gauge=("snotel", "784:CA:SNTL", "Palisades Tahoe SNOTEL (High Camp area)", 8010, 39.18986, -120.26576)),
    "alpine": dict(mid=(39.1567, -120.2431, 7500, "Mid-mountain"),
                   gauge=("snotel", "848:CA:SNTL", "Ward Creek #3 SNOTEL", 6750, 39.13545, -120.21865)),
    "kirkwood": dict(mid=(38.6750, -120.0630, 8300, "Mid-mountain"),
                     gauge=("snotel", "1067:CA:SNTL", "Carson Pass SNOTEL", 8360, 38.6927, -120.0022)),
    "mammoth": dict(mid=(37.6385, -119.0215, 9600, "McCoy Station"),
                    gauge=("cdec", "MHP", "Mammoth Pass (CDEC)", 9400, 37.61, -119.033), afd_office="REV"),
    "lakelouise": dict(gauge=("abrivers", "05BA815:158817042", "Pika Run (Alberta Rivers)", 7382, 51.462944, -116.117833)),
}


@dataclass
class Point:
    lat: float
    lon: float
    ft: int
    label: str


@dataclass
class Gauge:
    kind: str  # "snotel" | "cdec" | "bcasws" | "abrivers"
    id: str
    name: str
    ft: int
    lat: float
    lon: float


@dataclass
class Resort:
    key: str
    name: str
    region: str
    tz: str
    base: Point
    mid: Point
    summit: Point
    gauge: Gauge | None
    models: list
    dem_ft: dict = field(default_factory=dict)
    afd_office: str | None = None

    @property
    def us(self):
        return self.region not in NON_US

    def point(self, elev):
        return getattr(self, elev)

    @classmethod
    def from_json(cls, key, d):
        name, region = CATALOG[key][:2] if key in CATALOG else (d["name"], d["region"])  # catalog wins
        return cls(key=key, name=name, region=region, tz=d["tz"],
                   **{e: Point(**d[e]) for e in ELEVS}, gauge=Gauge(*d["obs"]) if d.get("obs") else None,
                   models=d["models"], dem_ft=d.get("dem_ft", {}), afd_office=d.get("afd_office"))

    def to_json(self):
        d = dict(name=self.name, region=self.region, tz=self.tz, **{e: asdict(self.point(e)) for e in ELEVS},
                 dem_ft=self.dem_ft, obs=list(asdict(self.gauge).values()) if self.gauge else None, models=self.models)
        return d | ({"afd_office": self.afd_office} if self.afd_office else {})


@dataclass
class ModelCal:
    factor: float
    weight: float                    # blend weight for lead days 1-3
    mae72: float | None = None
    raw_mae72: float | None = None
    n: int = 0
    weight_ext: float | None = None  # blend weight for lead days 4+ (0: model doesn't reach that far)
    mae_ext: float | None = None     # daily MAE (mm) at lead days 4-7, after the bias factor


@dataclass
class Calibration:
    """Per-resort precipitation bias factors, blend weights and historical error quantiles."""
    models: dict  # model -> ModelCal
    station: str | None = None
    uncalibrated: bool | None = None  # True: regional default (no gauge or still pending)
    blend_mae72: float | None = None
    n_windows: int | None = None
    n_wet: int = 0
    ratio_q: dict = field(default_factory=dict)  # "0.05".."0.95" -> obs/forecast ratio
    ratio_bins: list = field(default_factory=list)  # [{lo_mm, hi_mm, n, q}] by forecast storm size
    peers: int | None = None
    peers_scope: str | None = None
    temp_bias_c: float | None = None      # station minus model daily mean temperature, all days
    temp_bias_wet_c: float | None = None  # same on storm days: shifts the profile (phase, snow level, ratio)
    temp_n_wet: int = 0
    temp_mae_c: list | None = None        # [raw, corrected] daily-mean temperature MAE at the gauge

    @property
    def calibrated(self):
        return not self.uncalibrated

    def weight(self, model, lead_days):
        m = self.models[model]
        return m.weight_ext if lead_days > EXT_LEAD_DAYS and m.weight_ext is not None else m.weight

    @property
    def profile_shift_c(self):
        """Temperature correction applied to the model profile, capped (station siting adds its own bias)."""
        return max(-1.5, min(1.5, self.temp_bias_wet_c)) if self.temp_bias_wet_c is not None else 0.0

    def error_quantiles(self, liq72_mm, qs):
        """obs/forecast ratio quantiles for a 72 h storm of this size (falls back to all storms)."""
        rq = next((b["q"] for b in self.ratio_bins if b["lo_mm"] <= liq72_mm < b["hi_mm"]), self.ratio_q)
        return {q: rq.get(str(q), 1.0) for q in qs}

    @classmethod
    def from_json(cls, d):
        known = lambda c, x: {k: v for k, v in x.items() if k in {f.name for f in fields(c)}}
        return cls(**known(cls, d) | {"models": {m: ModelCal(**known(ModelCal, v)) for m, v in d["models"].items()}})

    def to_json(self):
        return {k: v for k, v in asdict(self).items() if v is not None}


def _load(name):
    p = os.path.join(DATA, name)
    return json.load(open(p)) if os.path.exists(p) else {}


def load_resorts():
    return {k: Resort.from_json(k, v) for k, v in _load("resorts.json").items()}


def load_calibration():
    return {k: Calibration.from_json(v) for k, v in _load("calibration.json").items()}


RESORTS = load_resorts()
