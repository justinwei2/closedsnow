"""Expert-worthy western North America resorts on Ikon / Epic.

Coordinates are approximate guesses; locate.py snaps each point to Open-Meteo's 90 m DEM
(base/summit within a search radius, closest to the published elevation; mid along the
base->summit line) and writes data/resorts.json. Published elevations are lift-served (ft).

Fields: key: (name, pass, region, tz, (base lat, lon, ft, label), (summit lat, lon, ft, label), sub)
"""
PT, MT, AK = "America/Los_Angeles", "America/Denver", "America/Anchorage"
VAN, EDM = "America/Vancouver", "America/Edmonton"

CATALOG = {
    # --- California / Nevada
    "palisades": ("Palisades Tahoe", "Ikon", "California", PT, (39.1969, -120.2354, 6200, "Village"), (39.1985, -120.2936, 9006, "Granite Chief"), "Olympic Valley side"),
    "alpine": ("Alpine Meadows", "Ikon", "California", PT, (39.1645, -120.2386, 6835, "Base lodge"), (39.1489, -120.2476, 8637, "Ward Peak"), "Palisades Tahoe – Alpine side"),
    "kirkwood": ("Kirkwood", "Epic", "California", PT, (38.6847, -120.0654, 7800, "Base"), (38.6650, -120.0610, 9876, "Thimble Peak ridge"), ""),
    "mammoth": ("Mammoth Mountain", "Ikon", "California", PT, (37.6510, -119.0270, 8900, "Main Lodge"), (37.6280, -119.0325, 11053, "Summit"), ""),
    # --- Pacific Northwest
    "bachelor": ("Mt. Bachelor", "Ikon", "Pacific Northwest", PT, (43.9922, -121.6786, 6300, "West Village"), (43.9790, -121.6886, 9065, "Summit"), ""),
    # --- Utah
    "alta": ("Alta", "Ikon", "Utah", MT, (40.5885, -111.6380, 8530, "Wildcat base"), (40.5770, -111.6300, 10550, "Upper mountain"), ""),
    "snowbird": ("Snowbird", "Ikon", "Utah", MT, (40.5812, -111.6555, 7760, "Base"), (40.5605, -111.6455, 11000, "Hidden Peak"), ""),
    "brighton": ("Brighton", "Ikon", "Utah", MT, (40.5980, -111.5832, 8755, "Base"), (40.5860, -111.5700, 10500, "Summit"), ""),
    "solitude": ("Solitude", "Ikon", "Utah", MT, (40.6197, -111.5917, 7988, "Base"), (40.6020, -111.5790, 10488, "Summit"), ""),
    "snowbasin": ("Snowbasin", "Ikon", "Utah", MT, (41.2163, -111.8570, 6400, "Base"), (41.2000, -111.8800, 9350, "Needles"), ""),
    "parkcity": ("Park City", "Epic", "Utah", MT, (40.6514, -111.5078, 6900, "Base"), (40.6265, -111.5590, 10026, "Jupiter Peak"), ""),
    # --- Wyoming / Montana / Idaho
    "jackson": ("Jackson Hole", "Ikon", "Wyoming · Montana · Idaho", MT, (43.5877, -110.8279, 6311, "Teton Village"), (43.5970, -110.8710, 10450, "Rendezvous"), ""),
    "bigsky": ("Big Sky", "Ikon", "Wyoming · Montana · Idaho", MT, (45.2857, -111.4012, 7500, "Mountain Village"), (45.2784, -111.4502, 11166, "Lone Peak"), ""),
    # --- Colorado
    "vail": ("Vail", "Epic", "Colorado", MT, (39.6405, -106.3743, 8120, "Vail Village"), (39.6080, -106.3560, 11570, "Summit"), ""),
    "breckenridge": ("Breckenridge", "Epic", "Colorado", MT, (39.4806, -106.0667, 9600, "Base"), (39.4586, -106.1048, 12840, "Imperial"), ""),
    "abasin": ("Arapahoe Basin", "Ikon", "Colorado", MT, (39.6420, -105.8715, 10780, "Base"), (39.6300, -105.8650, 13050, "Summit"), ""),
    "copper": ("Copper Mountain", "Ikon", "Colorado", MT, (39.5018, -106.1511, 9712, "Base"), (39.4720, -106.1660, 12313, "Tucker Mtn"), ""),
    "steamboat": ("Steamboat", "Ikon", "Colorado", MT, (40.4572, -106.8045, 6900, "Base"), (40.4563, -106.7485, 10568, "Mt. Werner"), ""),
    "aspenhighlands": ("Aspen Highlands", "Ikon", "Colorado", MT, (39.1823, -106.8556, 8040, "Base"), (39.1600, -106.8590, 11675, "Loge Peak"), "Aspen Snowmass"),
    "snowmass": ("Snowmass", "Ikon", "Colorado", MT, (39.2090, -106.9500, 8104, "Base Village"), (39.1645, -106.9670, 12510, "Big Burn / Cirque"), "Aspen Snowmass"),
    "aspenmountain": ("Aspen Mountain", "Ikon", "Colorado", MT, (39.1862, -106.8183, 7945, "Base"), (39.1530, -106.8170, 11212, "Summit"), "Aspen Snowmass"),
    "telluride": ("Telluride", "Epic · Ikon", "Colorado", MT, (37.9369, -107.8467, 8725, "Mountain Village"), (37.9105, -107.8380, 12570, "Gold Hill"), ""),
    "crestedbutte": ("Crested Butte", "Epic", "Colorado", MT, (38.8991, -106.9650, 9375, "Base"), (38.8877, -106.9440, 12162, "North Face / High Lift"), ""),
    # --- New Mexico
    "taos": ("Taos Ski Valley", "Ikon", "New Mexico", MT, (36.5963, -105.4545, 9207, "Base"), (36.5720, -105.4440, 12481, "Kachina Peak"), ""),
    # --- Alaska
    "alyeska": ("Alyeska", "Ikon", "Alaska", AK, (60.9705, -149.0980, 250, "Base"), (60.9640, -149.0700, 2750, "Glacier Bowl"), ""),
    # --- British Columbia
    "whistler": ("Whistler", "Epic", "British Columbia", VAN, (50.1150, -122.9548, 2214, "Village"), (50.0610, -122.9480, 7160, "Whistler Peak"), "Whistler Blackcomb"),
    "blackcomb": ("Blackcomb", "Epic", "British Columbia", VAN, (50.1155, -122.9480, 2214, "Village"), (50.0950, -122.8880, 7494, "Glacier / 7th Heaven"), "Whistler Blackcomb"),
    "revelstoke": ("Revelstoke", "Ikon", "British Columbia", VAN, (50.9580, -118.1636, 1680, "Base"), (50.9390, -118.2310, 7300, "Summit"), ""),
    "kickinghorse": ("Kicking Horse", "Epic", "British Columbia", EDM, (51.2975, -117.0480, 3900, "Base"), (51.2830, -117.0920, 8033, "Terminator Peak"), ""),
    "redmountain": ("RED Mountain", "Ikon", "British Columbia", VAN, (49.1030, -117.8195, 3888, "Base"), (49.1180, -117.8420, 6800, "Grey Mountain"), ""),
    "fernie": ("Fernie", "Epic", "British Columbia", EDM, (49.4627, -115.0870, 3500, "Base"), (49.4480, -115.1200, 7000, "Polar Peak"), ""),
    "panorama": ("Panorama", "Ikon", "British Columbia", EDM, (50.4600, -116.2380, 3800, "Base"), (50.4480, -116.2970, 7800, "Summit"), ""),
    "sunpeaks": ("Sun Peaks", "Ikon", "British Columbia", VAN, (50.8840, -119.8930, 4100, "Village"), (50.9030, -119.8510, 6824, "Tod Mountain"), ""),
    # --- Alberta
    "sunshine": ("Banff Sunshine", "Ikon", "Alberta", EDM, (51.0785, -115.7825, 7082, "Sunshine Village"), (51.0640, -115.7650, 8954, "Lookout Mountain"), ""),
    "lakelouise": ("Lake Louise", "Ikon", "Alberta", EDM, (51.4420, -116.1620, 5400, "Base"), (51.4615, -116.1290, 8650, "Top of the World"), ""),
}

# Hand-verified mid points and gauges for the original resorts (kept as-is by locate.py).
MANUAL = {
    "palisades": dict(mid=(39.1899, -120.2658, 8000, "High Camp"),
                      obs=("snotel", "784:CA:SNTL", "Palisades Tahoe SNOTEL (High Camp area)", 8010, 39.18986, -120.26576)),
    "alpine": dict(mid=(39.1567, -120.2431, 7500, "Mid-mountain"),
                   obs=("snotel", "848:CA:SNTL", "Ward Creek #3 SNOTEL", 6750, 39.13545, -120.21865)),
    "kirkwood": dict(mid=(38.6750, -120.0630, 8300, "Mid-mountain"),
                     obs=("snotel", "1067:CA:SNTL", "Carson Pass SNOTEL", 8360, 38.6927, -120.0022)),
    "mammoth": dict(mid=(37.6385, -119.0215, 9600, "McCoy Station"),
                    obs=("cdec", "MHP", "Mammoth Pass (CDEC)", 9400, 37.61, -119.033), afd_office="REV"),
}
FAVORITES = ["palisades", "alpine", "kirkwood", "mammoth"]  # refreshed on schedule; others on demand
