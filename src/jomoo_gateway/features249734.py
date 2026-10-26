"""Finite model-specific operations derived from public protocol observations.

These definitions are not hardware acceptance. Deployment allowlists remain separate.
No arbitrary opcodes, caller-supplied packets, firmware or factory-reset operations.
"""

# name: (FA byte, mask, shift, minimum, maximum)
LEVELS = {
    "water_temperature": (0, 0x70, 4, 0, 5),
    "seat_temperature": (1, 0x07, 0, 0, 5),
    "dryer_temperature": (2, 0x07, 0, 0, 3),
    "wash_pressure": (2, 0x70, 4, 1, 5),
    "nozzle_position": (1, 0x70, 4, 1, 5),
    "wash_width": (7, 0x07, 0, 1, 5),
}
FLAGS = {
    "automatic_lid": (0, 0x08),
    "automatic_night_light": (2, 0x80),
    "automatic_flush": (0, 0x02),
    "foot_sensor": (7, 0x20),
    "automatic_small_flush": (7, 0x10),
    "close_lid_flush": (4, 0x02),
    "automatic_foam": (6, 0x08),
    "automatic_deodorization": (4, 0x01),
    "season_temperature": (5, 0x08),
}
# Explicit set operations, unlike the two fresh-state toggles below.
FLAG_PACKETS = {
    "foot_sensor": (0x24, 1, 2),
    "automatic_small_flush": (0x25, 4, 8),
    "close_lid_flush": (0x25, 1, 2),
    "automatic_foam": (0x20, 2, 4),
}
TOGGLES = {"automatic_deodorization": 0x12, "season_temperature": 0x02}
STARTS = {
    "flush_large": (0x0E, {"large_flush_running": True}),
    "flush_small": (0x0F, {"small_flush_running": True}),
    "wash_hip": (0x08, {"wash_mode": "hip", "automatic_cycle": False}),
    "wash_bidet": (0x0B, {"wash_mode": "bidet", "automatic_cycle": False}),
    "wash_assist": (0x0A, {"wash_mode": "assist", "automatic_cycle": False}),
    "wash_auto_hip": (0x0C, {"wash_mode": "hip", "automatic_cycle": True}),
    "wash_auto_bidet": (0x07, {"wash_mode": "bidet", "automatic_cycle": True}),
    "dry_start": (0x04, {"wash_mode": "dry", "automatic_cycle": False}),
    "nozzle_clean": (0x09, {"wash_mode": "nozzle_clean"}),
    "nozzle_replace": (0x16, {"wash_mode": "nozzle_replace"}),
}
OCCUPIED_STARTS = frozenset(k for k in STARTS if k.startswith("wash_") or k == "dry_start")
SPECIAL_TARGETS = {
    "foam_start": {"foam_running": True},
    "self_clean": {"self_clean_running": True},
    "stop": {"work_idle": True},
    "move_on": {"wash_moving": True},
    "move_off": {"wash_moving": False},
}
ACTIONS = frozenset(
    {"lid_open", "lid_close", "ring_open", "ring_close"}
    | set(STARTS)
    | set(SPECIAL_TARGETS)
    | {f"{name}_{v}" for name in FLAGS for v in ("on", "off")}
    | {f"{name}_{v}" for name, (_, _, _, lo, hi) in LEVELS.items() for v in range(lo, hi + 1)}
)


def setting(action: str, *, woman: bool = False) -> tuple[str, int, int, int, int] | None:
    """Return field, FA byte, mask, shift and value for one exact allowed setting."""
    if action not in ACTIONS:
        return None
    name, _, raw = action.rpartition("_")
    if name in FLAGS:
        index, mask = FLAGS[name]
        return name, index, mask, 0, int(raw == "on")
    if name in LEVELS:
        index, mask, shift, _, _ = LEVELS[name]
        if woman and name in {"wash_pressure", "nozzle_position"}:
            index, mask, shift = (3, 0xF0, 4) if name == "wash_pressure" else (3, 0x0F, 0)
        return name + "_level", index, mask, shift, int(raw)
    return None
