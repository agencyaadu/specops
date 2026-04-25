import json
import logging
import os

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = [
    "ID", "Created", "Name", "WhatsApp", "Email", "Alt Email", "Occupation", "Google ID",
    "Telegram", "Discord", "X/Twitter", "Referred By",
    "Languages", "Hardest Problem", "Health Notes",
    "Address Line 1", "Address Line 2", "Pincode", "City", "State",
    "UPI", "Beneficiary", "Account Number", "IFSC", "Bank", "Branch",
    "PAN Number", "PAN Card URL", "Profile Picture URL", "Intro Video URL",
    "Consented KYC", "Consented Terms",
]

KEYS = [
    "id", "created_at", "full_name", "whatsapp", "email", "alt_email", "occupation", "google_id",
    "telegram_id", "discord_id", "twitter_id", "referred_by",
    "languages", "hardest_problem", "health_notes",
    "address_line1", "address_line2", "pincode", "city", "state",
    "upi_id", "beneficiary_name", "account_number", "ifsc_code", "bank_name", "branch_name",
    "pan_number", "pan_card_url", "profile_picture_url", "intro_video_url",
    "consented", "consented_terms",
]


def sheets_enabled() -> bool:
    return bool(os.environ.get("GOOGLE_SHEETS_ID") and os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))


def _service():
    from googleapiclient.discovery import build
    from google.oauth2.service_account import Credentials
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _sheet_id() -> str:
    return os.environ["GOOGLE_SHEETS_ID"]


def _cell(val) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return "yes" if val else "no"
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    return str(val)


def _to_row(d: dict) -> list:
    return [_cell(d.get(k)) for k in KEYS]


def ensure_header() -> None:
    svc = _service()
    sid = _sheet_id()
    result = svc.spreadsheets().values().get(
        spreadsheetId=sid, range="A1:A1"
    ).execute()
    if not result.get("values") or result["values"][0][0] != "ID":
        svc.spreadsheets().values().update(
            spreadsheetId=sid, range="A1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()


def append_row(d: dict) -> None:
    svc = _service()
    sid = _sheet_id()
    # range="Sheet1!A:AE" + insertDataOption=OVERWRITE makes Google scan the
    # whole column range, find the last non-empty row of the table, and write
    # the new row immediately after it. Using range="A1" + INSERT_ROWS causes
    # the row to be inserted right after A1 (i.e. at row 2), pushing data down.
    svc.spreadsheets().values().append(
        spreadsheetId=sid, range="Sheet1!A:AE",
        valueInputOption="RAW",
        insertDataOption="OVERWRITE",
        body={"values": [_to_row(d)]},
    ).execute()


def full_sync(rows: list) -> None:
    svc = _service()
    sid = _sheet_id()
    data = [HEADERS] + [_to_row(r) for r in rows]
    svc.spreadsheets().values().update(
        spreadsheetId=sid, range="A1",
        valueInputOption="RAW",
        body={"values": data},
    ).execute()


# ---------------------------------------------------------------------------
# Operations tab — lives in the same spreadsheet as onboarding submissions.
# Mirrors the operations table + comma-joined chief/captain assignments per op.
# ---------------------------------------------------------------------------

OPS_TAB = "Operations"

HEADERS_OPS = [
    "Op ID", "Factory", "Shift", "Location", "Map Link", "WhatsApp Group",
    "POC1 Name", "POC1 Phone", "POC1 Role",
    "POC2 Name", "POC2 Phone", "POC2 Role",
    "Sales Team",
    "Shift Start", "Shift End", "Reporting", "Deployment Start",
    "Collection Start", "Report Submission", "Final Closing",
    "Active", "Created",
    "Chiefs", "Captains",
]

KEYS_OPS = [
    "op_id", "factory_name", "shift", "location", "map_link", "whatsapp_group_url",
    "poc1_name", "poc1_phone", "poc1_role",
    "poc2_name", "poc2_phone", "poc2_role",
    "sales_team_name",
    "shift_start", "shift_end", "reporting_time", "deployment_start",
    "collection_start", "report_submission_time", "final_closing_time",
    "is_active", "created_at",
    "chiefs", "captains",
]


def _to_row_ops(d: dict) -> list:
    return [_cell(d.get(k)) for k in KEYS_OPS]


def _col_letter(n: int) -> str:
    """1-indexed column number -> A1-style letters (handles AA, AB, ...)."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(ord("A") + r) + s
    return s


def _ensure_ops_tab(svc, sid: str) -> None:
    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if OPS_TAB in titles:
        return
    svc.spreadsheets().batchUpdate(
        spreadsheetId=sid,
        body={"requests": [{"addSheet": {"properties": {"title": OPS_TAB}}}]},
    ).execute()


def full_sync_ops(rows: list) -> None:
    """Overwrite the Operations tab with the given ops dicts. Headers + data."""
    svc = _service()
    sid = _sheet_id()
    _ensure_ops_tab(svc, sid)
    data = [HEADERS_OPS] + [_to_row_ops(r) for r in rows]
    last_col = _col_letter(len(HEADERS_OPS))
    # Clear first so a smaller dataset than last time doesn't leave stale rows.
    svc.spreadsheets().values().clear(
        spreadsheetId=sid, range=f"{OPS_TAB}!A:{last_col}",
    ).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=sid, range=f"{OPS_TAB}!A1",
        valueInputOption="RAW",
        body={"values": data},
    ).execute()
