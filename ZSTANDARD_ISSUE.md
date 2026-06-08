# zstandard Auto-Install Workaround

## Problem

Home Assistant 2026.3+ ships with Python 3.14 inside its Docker container. HA's internal pip resolver uses a custom wheel index (`wheels.home-assistant.io`) which currently only provides `cp313` wheels for `zstandard`, not `cp314`. This causes the integration to fail loading with:

```
RequirementsNotFound: Requirements for typhur_sync_gold_ble not found: ['zstandard==0.25.0']
```

## Affected Versions

- **Home Assistant:** 2026.3+ (Python 3.14)
- **pip (bundled):** 26.0.1 (does not fall back to PyPI properly)
- **Python:** 3.14 (cp314)
- **zstandard:** 0.25.0 (has cp314 wheels on PyPI, but not in HA's custom index)

## Workaround

The integration includes a fallback mechanism in `__init__.py` that catches the `ImportError` and installs `zstandard` via pip directly (which does reach PyPI).

This means `zstandard` is **not** listed in `manifest.json` `requirements`, so HA's loader won't attempt (and fail) to install it. Instead, the integration handles installation itself on first load.

## Revert Plan

Once Home Assistant updates their wheel index to include `cp314` wheels for `zstandard`:

1. Remove `_ensure_zstandard()` and related code from `__init__.py`
2. Add `zstandard==0.25.0` back to `requirements` in `manifest.json`
3. Remove this file

## Manual Fix (for users)

If the auto-install fails for any reason, users can run:

```bash
docker exec homeassistant pip3 install --upgrade pip
docker exec homeassistant pip3 install zstandard==0.25.0
```

Then restart Home Assistant.
