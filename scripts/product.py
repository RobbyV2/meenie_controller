import hashlib, json, pathlib, re


COPPER = (
    ("F_Cu.gtl", "Copper,L1,Top"),
    ("GND.g1", "Copper,L2,Inr"),
    ("GND2.g2", "Copper,L3,Inr"),
    ("HDI_PWR.g3", "Copper,L4,Inr"),
    ("CORE_ROUTE_L5.g4", "Copper,L5,Inr"),
    ("GPIO_ROUTE_L6.g5", "Copper,L6,Inr"),
    ("RF_GND_L7.g6", "Copper,L7,Inr"),
    ("B_Cu.gbl", "Copper,L8,Bot"),
)
LASER_DRILLS = (
    ("front-in1", "Plated,1,2,Blind"),
    ("in1-in2", "Plated,2,3,Buried"),
    ("in2-in3", "Plated,3,4,Buried"),
    ("in4-in5", "Plated,5,6,Buried"),
    ("in5-in6", "Plated,6,7,Buried"),
    ("back-in6", "Plated,7,8,Blind"),
)
CENTRAL_DRILL = ("in3-in4", "Plated,4,5,Buried")


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def load_manifest(path):
    return json.loads(pathlib.Path(path).read_text())


def has_central(manifest):
    return bool(manifest.get("central_buried_transitions"))


def drill_specs(manifest):
    drills = list(LASER_DRILLS)
    if has_central(manifest):
        drills.insert(3, CENTRAL_DRILL)
    return (("PTH", "Plated,1,8,PTH"), *drills, ("NPTH", "NonPlated,1,8,NPTH"))


def file_function(path):
    match = re.search(r"TF.FileFunction,([^*%\r\n]+)", pathlib.Path(path).read_text(errors="replace"))
    return match.group(1) if match else None
