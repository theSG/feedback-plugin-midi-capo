"""MIDI Capo plugin — extract tuning offsets from PSARC or sloppak."""

import json
from functools import lru_cache
from pathlib import Path


def _apply_cent_offset(offsets, cent_offset, arr_name):
    """Adjust tuning offsets by CentOffset (virtual capo correction)."""
    if not cent_offset:
        return offsets
    shift = round(cent_offset / 100)
    n_strings = 4 if arr_name == "Bass" else 6
    return [o + shift if i < n_strings else o
            for i, o in enumerate(offsets)]


@lru_cache(maxsize=256)
def _parse_sloppak_tunings(sloppak_path: str) -> dict[str, tuple[int, ...]]:
    """Parse and cache all arrangement tunings from a sloppak manifest.

    Sloppak tunings already include any cent/semitone adjustment baked in
    (no separate CentOffset field), so no further correction is needed.
    """
    from sloppak import load_manifest
    manifest = load_manifest(Path(sloppak_path))
    arr_tunings: dict[str, tuple[int, ...]] = {}
    for entry in manifest.get("arrangements", []) or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name or name in ("Vocals", "ShowLights", "JVocals"):
            continue
        tun = entry.get("tuning")
        if not isinstance(tun, list) or not tun:
            continue
        # Normalize to 6 slots (sloppak spec allows 5/7-string content).
        offsets = [int(tun[i]) if i < len(tun) else 0 for i in range(6)]
        arr_tunings[name] = tuple(offsets)
    return arr_tunings


@lru_cache(maxsize=256)
def _parse_tunings(psarc_path: str) -> dict[str, tuple[int, ...]]:
    """Parse and cache all arrangement tunings from a PSARC."""
    from psarc import read_psarc_entries
    files = read_psarc_entries(psarc_path, ["*.json"])

    arr_tunings = {}
    for path, data in sorted(files.items()):
        if not path.endswith(".json"):
            continue
        try:
            j = json.loads(data)
        except json.JSONDecodeError:
            import re
            text = data.decode("utf-8", errors="ignore")
            text = re.sub(r",\s*([}\]])", r"\1", text)
            try:
                j = json.loads(text)
            except Exception:
                continue

        for k, v in j.get("Entries", {}).items():
            attrs = v.get("Attributes", {})
            arr_name = attrs.get("ArrangementName", "")
            if arr_name in ("Vocals", "ShowLights", "JVocals"):
                continue
            tun = attrs.get("Tuning")
            if tun and isinstance(tun, dict):
                offsets = [tun.get(f"string{i}", 0) for i in range(6)]
                cent_offset = attrs.get("CentOffset", 0.0) or 0.0
                offsets = _apply_cent_offset(offsets, cent_offset, arr_name)
                arr_tunings[arr_name] = tuple(offsets)

    return arr_tunings


def setup(app, context):

    @app.get("/api/plugins/midi_capo/tuning/{filename:path}")
    def get_tuning(filename: str, arrangement: str = ""):
        dlc = context["get_dlc_dir"]()
        if not dlc:
            return {"error": "DLC folder not configured"}

        song_path = dlc / filename
        if not song_path.exists():
            return {"error": "File not found"}

        lower = str(song_path).lower()
        try:
            if lower.endswith(".psarc"):
                arr_tunings = _parse_tunings(str(song_path))
            elif lower.endswith(".sloppak"):
                arr_tunings = _parse_sloppak_tunings(str(song_path))
            else:
                return {"tuning": [0, 0, 0, 0, 0, 0]}
        except (ValueError, OSError, KeyError):
            return {"tuning": [0, 0, 0, 0, 0, 0]}

        if not arr_tunings:
            return {"tuning": [0, 0, 0, 0, 0, 0]}

        if arrangement and arrangement in arr_tunings:
            return {"tuning": list(arr_tunings[arrangement])}
        for name in ("Lead", "Rhythm", "Combo"):
            if name in arr_tunings:
                return {"tuning": list(arr_tunings[name])}
        return {"tuning": list(next(iter(arr_tunings.values())))}
