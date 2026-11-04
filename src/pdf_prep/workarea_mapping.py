"""Validate optional EWMH coordinates without assuming they are physical pixels."""
import math


class InvalidWorkArea(ValueError):
    pass


def rectangle(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise InvalidWorkArea("rectangle-shape-invalid")
    if any(type(item) is not int or abs(item) > 262144 for item in value):
        raise InvalidWorkArea("rectangle-values-invalid")
    if value[2] <= 0 or value[3] <= 0:
        raise InvalidWorkArea("rectangle-size-invalid")
    return tuple(value)


def inside(inner, outer):
    return (inner[0] >= outer[0] and inner[1] >= outer[1]
            and inner[0] + inner[2] <= outer[0] + outer[2]
            and inner[1] + inner[3] <= outer[1] + outer[3])


def normalize_workarea(data, screens, target, explicit_scale=None):
    """Return an inward-rounded Qt rectangle or a bounded fallback reason.

    A shared scale is accepted only after comparing the complete root and every
    monitor rectangle. Mixed scaling, mirror ambiguity and monitor islands fail
    closed. The caller retains Qt's native availableGeometry on failure.
    """
    try:
        if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
            raise InvalidWorkArea("workarea-schema-invalid")
        if data.get("source") != "x11-ewmh" or data.get("coordinate_space") != "x11-root":
            raise InvalidWorkArea("workarea-source-unavailable")
        root, work = rectangle(data.get("root")), rectangle(data.get("workarea"))
        if root[:2] != (0, 0) or not inside(work, root):
            raise InvalidWorkArea("workarea-outside-root")
        if not isinstance(screens, list) or not 1 <= len(screens) <= 64 or not 0 <= target < len(screens):
            raise InvalidWorkArea("qt-screen-layout-invalid")
        rects = [rectangle(item["geometry"]) for item in screens]
        dprs = [float(item["dpr"]) for item in screens]
        if any(not math.isfinite(v) or not .25 <= v <= 16 for v in dprs):
            raise InvalidWorkArea("qt-device-scale-invalid")
        if max(dprs) - min(dprs) > .00001:
            raise InvalidWorkArea("mixed-device-scales-unverified")
        x, y = min(r[0] for r in rects), min(r[1] for r in rects)
        width = max(r[0] + r[2] for r in rects) - x
        height = max(r[1] + r[3] for r in rects) - y
        if len(set(rects)) != len(rects):
            raise InvalidWorkArea("mirrored-monitor-layout-ambiguous")
        raw_monitors = data.get("monitors")
        if raw_monitors is None and len(screens) == 1:
            monitors = [root]
        elif not isinstance(raw_monitors, list) or len(raw_monitors) != len(screens):
            raise InvalidWorkArea("native-monitor-layout-unverified")
        else:
            monitors = [rectangle(item) for item in raw_monitors]
        if any(not inside(monitor, root) for monitor in monitors) or len(set(monitors)) != len(monitors):
            raise InvalidWorkArea("native-monitor-layout-invalid")
        candidates = {1., dprs[0]}
        try:
            factor = float(explicit_scale)
            if math.isfinite(factor) and .25 <= factor <= 16:
                candidates.add(factor)
        except (TypeError, ValueError):
            pass
        valid = []
        for scale in candidates:
            if abs(width * scale - root[2]) > 2 or abs(height * scale - root[3]) > 2:
                continue
            unused = list(monitors)
            for rect in rects:
                mapped = ((rect[0] - x) * scale, (rect[1] - y) * scale, rect[2] * scale, rect[3] * scale)
                matches = [r for r in unused if all(abs(a - b) <= 2 for a, b in zip(r, mapped))]
                if len(matches) != 1:
                    break
                unused.remove(matches[0])
            else:
                if not unused:
                    left = max(rects[target][0], x + math.ceil(work[0] / scale))
                    top = max(rects[target][1], y + math.ceil(work[1] / scale))
                    right = min(rects[target][0] + rects[target][2], x + math.floor((work[0] + work[2]) / scale))
                    bottom = min(rects[target][1] + rects[target][3], y + math.floor((work[1] + work[3]) / scale))
                    if right > left and bottom > top:
                        valid.append(((left, top, right - left, bottom - top), scale))
        distinct = {entry[0] for entry in valid}
        if len(distinct) != 1:
            raise InvalidWorkArea("coordinate-mapping-unverified" if not valid else "coordinate-mapping-ambiguous")
        return valid[0][0], dict(source="x11-ewmh", coordinate_scale=valid[0][1],
                                validated_coordinate_mapping=True,
                                native_monitor_rectangles_checked=raw_monitors is not None)
    except (InvalidWorkArea, KeyError, TypeError, ValueError, OverflowError) as exc:
        code = str(exc) if isinstance(exc, InvalidWorkArea) else "workarea-response-invalid"
        return None, dict(source="captured-qt", reason=code)
