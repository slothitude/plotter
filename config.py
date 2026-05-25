"""Pen Plotter — Configuration, tool profiles, and calibration data."""

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import toml

BASE_DIR = Path(__file__).parent
PROFILES_DIR = BASE_DIR / "profiles"
CALIBRATION_FILE = BASE_DIR / "calibration.json"
OUTPUT_DIR = BASE_DIR / "output"


# ── Printer defaults ────────────────────────────────────────────────

PRINTER_BED_X = 220.0  # mm
PRINTER_BED_Y = 220.0  # mm
SAFE_Z = 30.0           # Minimum Z height for all non-drawing moves (travel)


# ── Page size presets ────────────────────────────────────────────────

PAGE_PRESETS = {
    "220mm": (220, 220),
    "A4": (210, 297),
    "A5": (148, 210),
    "Letter": (216, 279),
    "4x6": (102, 152),
    "5x7": (127, 178),
    "Custom": None,
}

PAGE_SIZE_FILE = BASE_DIR / "page_size.json"


def load_page_size() -> dict:
    """Load saved page size. Defaults to 220x220."""
    if PAGE_SIZE_FILE.exists():
        try:
            with open(PAGE_SIZE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"width": 220, "height": 220, "preset": "220mm", "offset_x": 0, "offset_y": 0}


def save_page_size(width: float, height: float, preset: str = "custom",
                   offset_x: float = 0, offset_y: float = 0):
    """Save page size to disk."""
    with open(PAGE_SIZE_FILE, "w") as f:
        json.dump({
            "width": float(width), "height": float(height), "preset": preset,
            "offset_x": float(offset_x), "offset_y": float(offset_y),
        }, f, indent=2)


# ── Data classes ────────────────────────────────────────────────────

@dataclass
class MovementConfig:
    draw_speed: float = 1500.0    # mm/min
    travel_speed: float = 3000.0  # mm/min
    lift_height: float = 5.0      # mm
    wear_rate: float = 0.0        # mm Z per meter drawn (0 = disabled)
    max_wear_depth: float = 0.0   # max Z lowering in mm (0 = uncapped)


@dataclass
class HeightConfig:
    pen_down_z: float = 0.0   # calibrated
    pen_up_z: float = 5.0     # = pen_down_z + lift_height
    offset_x: float = 0.0     # pen X offset from hotend
    offset_y: float = 0.0     # pen Y offset from hotend


@dataclass
class Pass2Config:
    """Second-pass (wet brush) settings for two-pass watercolor."""
    draw_speed: float = 800.0       # mm/min — slower for wet brushing
    travel_speed: float = 2500.0    # mm/min
    pen_down_z: float = 0.0         # may differ from pencil contact
    lift_height: float = 5.0        # mm
    change_z: float = 50.0          # mm — Z height during tool swap
    change_x: float = 110.0         # mm — X park position for tool swap
    change_y: float = 110.0         # mm — Y park position for tool swap


@dataclass
class WaterConfig:
    enabled: bool = False
    two_pass: bool = True               # enable two-pass watercolor mode
    cup_x: float = 0.0
    cup_y: float = 200.0
    cup_height: float = 15.0
    cup_diameter: float = 60.0
    dip_depth: float = 15.0
    dip_time: int = 500        # ms
    dip_interval: int = 50     # segments between dips
    scrape_distance: float = 15.0   # mm sideways at rim to shed excess water
    scrape_speed: float = 300.0     # mm/min — slow for bristle scrape
    pass2: Pass2Config = field(default_factory=Pass2Config)


@dataclass
class FillConfig:
    enabled: bool = False
    fill_type: str = "hatch"       # "hatch" or "crosshatch"
    spacing: float = 2.0           # mm between lines
    angle: float = 45.0            # degrees from horizontal


@dataclass
class ToolProfile:
    name: str = "Pencil"
    movement: MovementConfig = field(default_factory=MovementConfig)
    height: HeightConfig = field(default_factory=HeightConfig)
    water: WaterConfig = field(default_factory=WaterConfig)
    fill: FillConfig = field(default_factory=FillConfig)

    def recalc_pen_up(self):
        """Recalculate pen_up_z from pen_down_z + lift_height."""
        self.height.pen_up_z = self.height.pen_down_z + self.movement.lift_height


# ── Profile I/O ─────────────────────────────────────────────────────

def _profile_path(tool_name: str) -> Path:
    return PROFILES_DIR / f"{tool_name}.toml"


def list_profiles() -> list[str]:
    """List available tool profile names."""
    if not PROFILES_DIR.exists():
        return []
    return [p.stem for p in sorted(PROFILES_DIR.glob("*.toml"))]


def load_profile(tool_name: str) -> ToolProfile:
    """Load a tool profile from TOML, merging saved calibration data."""
    path = _profile_path(tool_name)
    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {path}")

    raw = toml.load(path)
    tool = raw.get("tool", {})
    mv = raw.get("movement", {})
    ht = raw.get("height", {})
    wt = raw.get("water", {})
    fl = raw.get("fill", {})

    # Parse pass2 separately (nested dataclass)
    pass2_raw = wt.pop("pass2", {})
    profile = ToolProfile(
        name=tool.get("name", tool_name.title()),
        movement=MovementConfig(**{k: v for k, v in mv.items() if k in MovementConfig.__dataclass_fields__}),
        height=HeightConfig(**{k: v for k, v in ht.items() if k in HeightConfig.__dataclass_fields__}),
        water=WaterConfig(**{k: v for k, v in wt.items() if k in WaterConfig.__dataclass_fields__}),
        fill=FillConfig(**{k: v for k, v in fl.items() if k in FillConfig.__dataclass_fields__}),
    )
    if pass2_raw:
        profile.water.pass2 = Pass2Config(**{k: v for k, v in pass2_raw.items() if k in Pass2Config.__dataclass_fields__})

    # Merge calibration data if saved
    cal = load_calibration()
    if tool_name in cal:
        profile.height.pen_down_z = cal[tool_name]["pen_down_z"]
        profile.height.pen_up_z = cal[tool_name].get("pen_up_z", profile.height.pen_down_z + profile.movement.lift_height)
        profile.height.offset_x = cal[tool_name].get("offset_x", 0.0)
        profile.height.offset_y = cal[tool_name].get("offset_y", 0.0)

    return profile


def save_profile(tool_name: str, profile: ToolProfile):
    """Save a tool profile back to TOML (base values only, not calibration overrides)."""
    path = _profile_path(tool_name)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)

    # Use raw TOML height values to avoid baking calibrated data into the base file
    try:
        raw_toml = toml.loads(path.read_text(encoding="utf-8"))
        raw_height = raw_toml.get("height", {})
    except Exception:
        raw_height = {}

    height_data = asdict(profile.height)
    # Override calibrated values with raw TOML base values
    for key in ("pen_down_z", "pen_up_z", "offset_x", "offset_y"):
        if key in raw_height:
            height_data[key] = raw_height[key]

    data = {
        "tool": {"name": profile.name},
        "movement": asdict(profile.movement),
        "height": height_data,
        "water": asdict(profile.water),
        "fill": asdict(profile.fill),
    }
    with open(path, "w") as f:
        toml.dump(data, f)


# ── Calibration I/O ─────────────────────────────────────────────────

def load_calibration() -> dict:
    """Load calibration data for all tools."""
    if not CALIBRATION_FILE.exists():
        return {}
    with open(CALIBRATION_FILE, "r") as f:
        return json.load(f)


def save_calibration(tool_name: str, pen_down_z: float, pen_up_z: float,
                     offset_x: float = 0.0, offset_y: float = 0.0):
    """Save calibration for a specific tool."""
    cal = load_calibration()
    cal[tool_name] = {
        "pen_down_z": round(pen_down_z, 3),
        "pen_up_z": round(pen_up_z, 3),
        "offset_x": round(offset_x, 3),
        "offset_y": round(offset_y, 3),
    }
    with open(CALIBRATION_FILE, "w") as f:
        json.dump(cal, f, indent=2)
