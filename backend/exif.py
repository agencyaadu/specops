import io
from typing import Optional, Tuple
from PIL import Image, ExifTags

import pillow_heif
pillow_heif.register_heif_opener()

_GPS_IFD_TAG = next((k for k, v in ExifTags.TAGS.items() if v == "GPSInfo"), 0x8825)
_GPS_TAGS = {v: k for k, v in ExifTags.GPSTAGS.items()}

def _to_degrees(rational) -> float:
    # PIL gives us (num, denom) tuples or IFDRational; float() works on both.
    d, m, s = (float(x) for x in rational)
    return d + m / 60.0 + s / 3600.0

def extract_gps(file_bytes: bytes) -> Optional[Tuple[float, float]]:
    """Return (lat, lng) from EXIF GPS tags, or None if absent/unreadable."""
    try:
        img = Image.open(io.BytesIO(file_bytes))
        exif = img.getexif()
    except Exception:
        return None

    if not exif:
        return None

    try:
        gps = exif.get_ifd(_GPS_IFD_TAG)
    except Exception:
        gps = None
    if not gps:
        return None

    # Keys can be raw numeric IDs; map them to names.
    named = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps.items()}

    lat = named.get("GPSLatitude")
    lat_ref = named.get("GPSLatitudeRef")
    lng = named.get("GPSLongitude")
    lng_ref = named.get("GPSLongitudeRef")

    if not (lat and lng and lat_ref and lng_ref):
        return None

    try:
        lat_val = _to_degrees(lat)
        lng_val = _to_degrees(lng)
    except Exception:
        return None

    if str(lat_ref).upper().startswith("S"):
        lat_val = -lat_val
    if str(lng_ref).upper().startswith("W"):
        lng_val = -lng_val

    return (lat_val, lng_val)
