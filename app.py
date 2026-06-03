from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html import escape
from io import BytesIO
import hmac
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from requests.auth import HTTPBasicAuth, HTTPDigestAuth


# =============================================================================
# Configuration
# =============================================================================

APP_TITLE = "ESG - metrics"
APP_DIR = Path(__file__).resolve().parent
DEFAULT_BACKGROUND_IMAGE = APP_DIR / "06626d6c-ataman-dpg-panathinaikos-olympiacos.jpg"
ODATA_ENDPOINT = "https://online.marorka.com/Odata/v1/ODataService.svc/ReportData"
MAX_ODATA_PAGES = 250
API_CACHE_TTL_SECONDS = 21600  # 6 hours; KPI filters use local data and do not refetch.
UI_DATE_INPUT_FORMAT = "DD/MM/YYYY"
DISPLAY_DATETIME_FORMAT = "%d/%m/%Y %H:%M"
API_FULL_START_DATE = date(2026, 1, 1)
TABLE_PREVIEW_ROW_LIMIT = 500



EXCLUDED_REPORT_TYPES = [
    "Intake Report",
    "Fuel Change Report",
]

SOURCE_COLUMNS = [
    "ReportId",
    "ShipName",
    "ReportType",
    "StartDateTimeGMT",
    "EndDateTimeGMT",
    "LapTime",
    "StateName",
    "ValueDescription",
    "ReportedValue",
]

# Required API values only. The API request remains simple; these are lied locally
# after the data is downloaded, mimicking the stable working reefer  pattern.
VALUE_ALIASES = {
    # Canonical  column: API ValueDescription aliases to accept.
    # Units in Marorka are usually cbm; displayed as m3 for the colleague-facing report.
    "Water Consumed (m3)": [
        "FW Consumed [cbm]",
        "FW Consumed",
        "Fresh Water Consumed [cbm]",
        "Fresh Water Consumed",
        "Water Consumed [cbm]",
        "Water Consumed [m3]",
    ],
    "Water Produced (m3)": [
        "FW Produced [cbm]",
        "FW Produced",
        "Fresh Water Produced [cbm]",
        "Fresh Water Produced",
        "Water Produced [cbm]",
        "Water Produced [m3]",
    ],
    "Water Supplied (m3)": [
        "FW Received [cbm]",
        "FW Received",
        "Fresh Water Received [cbm]",
        "Fresh Water Received",
        "Water Supplied [cbm]",
        "Water Supplied [m3]",
    ],
    "Sludge": [
        "Sludge Produced [cbm]",
        "Sludge Produced",
        "Sludge [cbm]",
        "Sludge [m3]",
    ],
    "Bilge": [
        "Bilge Water Produced [cbm]",
        "Bilge Water Produced",
        "Bilge [cbm]",
        "Bilge [m3]",
    ],
}

WATER_WASTE_COLUMNS = list(VALUE_ALIASES.keys())

ME_FUEL_COLUMNS: list[str] = []
BOILER_FUEL_COLUMNS: list[str] = []

DISPLAY_COLUMNS = [
    "ShipName",
    "ReportType",
    "StartDateTimeGMT",
    "EndDateTimeGMT",
    "LapTime",
    "StateName",
    "Water Consumed (m3)",
    "Water Produced (m3)",
    "Water Supplied (m3)",
    "Sludge",
    "Bilge",
]

VESSEL_GROUPS = {
    "Fleet 1": ["ATETI", "CMA CGM THALASSA", "CZECH", "DOLPHIN II", "GSL CHRISTEL ELISABETH", "GSL VINIA", "MYNY", "SYDNEY EXPRESS"],
    "Fleet 2": ["AGIOS DIMITRIOS", "ELENI T", "MAIRA", "MELINA", "NEWYORKER", "NIKOLAS", "TORRANCE"],
    "Fleet 3": ["BREMERHAVEN EXPRESS", "CMA CGM ALCAZAR", "GSL ALICE", "GSL CHATEAU D'IF", "GSL ELEFTHERIA", "GSL MAREN", "GSL MELINA", "ISTANBUL EXPRESS"],
    "Fleet 4": ["ANTHEA Y", "COLOMBIA EXPRESS", "COSTA RICA EXPRESS", "JAMAICA EXPRESS", "MEXICO EXPRESS", "NICARAGUA EXPRESS", "PANAMA EXPRESS", "ZIM NORFOLK", "ZIM XIAMEN"],
    "Fleet 9": ["CMA CGM AMERICA", "CMA CGM SAMBHAR", "GSL ELENI", "GSL GRANIA", "GSL KALLIOPI", "GSL NINGBO", "MSC QINGDAO", "MSC TIANJIN"],
    "Fleet 10": ["CAPTAIN THANASIS I", "CMA CGM JAMAICA", "GSL CHRISTEN", "GSL NICOLETTA", "GSL VALERIE", "JULIE", "KUMASI", "MANET"],
    "Fleet 11": ["ATHENA", "EPAMINONDAS", "IAN H", "MARIANNA I", "MSC ROMA", "TINA I"],
    "Fleet 12": ["GSL DOROTHEA", "GSL KITHIRA", "GSL MARIA", "GSL MELITA", "GSL SYROS", "GSL TEGEA", "GSL TINOS", "GSL TRIPOLI"],
    "Fleet 14": ["GSL CHLOE", "GSL ELIZABETH", "GSL MAMITSA", "GSL MERCER", "GSL ROSSI", "GSL SUSAN", "TONSBERG"],
    "Fleet 15": ["GSL ALEXANDRA", "GSL ARCADIA", "GSL EFFIE", "GSL LYDIA", "GSL MYNY", "GSL SOFIA", "GSL VIOLETTA", "KOSTAS K", "MARIA Y"],
}

VESSEL_OPTIONS = sorted({v for vessels in VESSEL_GROUPS.values() for v in vessels})

# Default report filters. These are intentionally light: the colleague-facing
#  should show all fetched water/waste rows unless the user adds filters.
DEFAULT_REPORT_FILTER_COLUMNS: list[str] = []
DEFAULT_REPORT_NUMERIC_FILTERS: dict[str, dict[str, str]] = {}
DEFAULT_REPORT_CATEGORICAL_FILTERS: dict[str, list[str]] = {}


st.set_page_config(page_title=APP_TITLE, layout="wide")


# =============================================================================
# Styling
# =============================================================================


def apply_custom_css() -> None:
    background_image_url = dashboard_background_image_url()
    background_image_layer = dashboard_background_image_layer(background_image_url)
    hero_background = dashboard_hero_background(has_background_image=bool(background_image_url))
    hero_backdrop_filter = dashboard_hero_backdrop_filter(has_background_image=bool(background_image_url))
    hero_box_shadow = dashboard_hero_box_shadow(has_background_image=bool(background_image_url))
    metric_background = dashboard_metric_background(has_background_image=bool(background_image_url))
    metric_backdrop_filter = dashboard_metric_backdrop_filter(has_background_image=bool(background_image_url))
    metric_box_shadow = dashboard_metric_box_shadow(has_background_image=bool(background_image_url))
    st.markdown(
        """
        <style>
        :root {
            --bg: #050505;
            --panel: #10100C;
            --panel-soft: #19170F;
            --border: rgba(0, 212, 106, 0.24);
            --text-soft: #B8B29F;
            --cyan: #00D46A;
            --green: #00A85A;
            --red-muted: rgba(207, 95, 95, 0.24);
        }

        .stApp {
            background:
                __BACKGROUND_IMAGE_LAYER__
                radial-gradient(circle at top left, rgba(0, 212, 106, 0.13), transparent 34rem),
                radial-gradient(circle at top right, rgba(0, 168, 90, 0.10), transparent 30rem),
                linear-gradient(180deg, rgba(0, 212, 106, 0.04), transparent 22rem),
                var(--bg);
            background-position: center center;
            background-size: cover;
            background-attachment: fixed;
        }

        header[data-testid="stHeader"] {
            background: transparent !important;
            border-bottom: 0 !important;
            box-shadow: none !important;
            backdrop-filter: none;
        }

        header[data-testid="stHeader"] > div {
            background: transparent !important;
            border: 0 !important;
            box-shadow: none !important;
        }

        div[data-testid="stToolbar"] {
            background: transparent !important;
        }

        div[data-testid="stDecoration"] {
            background: transparent !important;
            height: 0 !important;
        }

        div[data-testid="stAlert"],
        div[data-testid="stAlert"] > div,
        div[data-testid="stAlert"] [role="alert"],
        div[data-testid="stAlertContentInfo"],
        div[data-testid="stAlertContentWarning"],
        div[data-testid="stAlertContentError"],
        div[data-testid="stAlertContentSuccess"] {
            background: transparent !important;
            background-color: transparent !important;
            background-image: none !important;
            border: 0 !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            color: #FFFBEA !important;
            backdrop-filter: none;
        }

        div[data-testid="stAlert"] {
            padding-left: 0 !important;
            padding-right: 0 !important;
        }

        div[data-testid="stAlert"] * {
            background: transparent !important;
            background-color: transparent !important;
            background-image: none !important;
            border: 0 !important;
            box-shadow: none !important;
        }

        div[data-testid="stAlert"] svg {
            display: none !important;
        }

        div[data-testid="stAlert"] div,
        div[data-testid="stAlert"] p {
            color: #FFFBEA !important;
            font-weight: 700 !important;
            text-shadow: 0 2px 12px rgba(0,0,0,0.92);
        }

        .block-container {
            padding-top: 3.2rem;
            padding-bottom: 3rem;
            max-width: 1280px;
        }

        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #11100A 0%, #050505 100%);
            border-right: 1px solid var(--border);
        }

        section[data-testid="stSidebar"] > div {
            padding-bottom: 8rem !important;
        }

        section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
            gap: 0.7rem;
        }

        section[data-testid="stSidebar"] div[data-baseweb="select"] > div {
            overflow: visible !important;
        }

        section[data-testid="stSidebar"] [data-testid="stExpander"] {
            margin-bottom: 0.45rem;
        }

        section[data-testid="stSidebar"] label {
            color: #F5EFD8 !important;
            font-weight: 700 !important;
        }

        /* Inputs: calm by default; one homogeneous green outline only on focus. */
        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div {
            background-color: rgba(13, 13, 9, 0.88) !important;
            border: 1px solid rgba(0, 212, 106, 0.16) !important;
            border-radius: 14px !important;
            box-shadow: none !important;
            outline: none !important;
            overflow: hidden !important;
            transition: border-color 140ms ease, box-shadow 140ms ease, background-color 140ms ease !important;
        }

        /* Keep the actual inner input flat so BaseWeb does not draw a second rectangle. */
        div[data-baseweb="input"] input,
        [data-testid="stTextInput"] input,
        [data-testid="stDateInput"] input,
        textarea {
            background: transparent !important;
            background-color: transparent !important;
            border: 0 !important;
            box-shadow: none !important;
            outline: none !important;
            caret-color: #00D46A !important;
        }

        div[data-baseweb="select"] > div:hover,
        div[data-baseweb="input"] > div:hover {
            border-color: rgba(0, 212, 106, 0.24) !important;
            box-shadow: none !important;
        }

        div[data-baseweb="select"] > div:focus-within,
        div[data-baseweb="input"] > div:focus-within,
        div[data-baseweb="input"]:focus-within > div {
            border-color: rgba(0, 212, 106, 0.88) !important;
            box-shadow: 0 0 0 1px rgba(0, 212, 106, 0.64) !important;
            outline: none !important;
        }

        div[data-baseweb="input"] input:focus,
        div[data-baseweb="input"] input:focus-visible,
        [data-testid="stTextInput"] input:focus,
        [data-testid="stTextInput"] input:focus-visible,
        [data-testid="stDateInput"] input:focus,
        [data-testid="stDateInput"] input:focus-visible,
        textarea:focus,
        textarea:focus-visible {
            border: 0 !important;
            outline: none !important;
            box-shadow: none !important;
        }

        /* Make the password-eye/button area part of the same input surface. */
        div[data-baseweb="input"] button,
        [data-testid="stTextInput"] button,
        div[data-baseweb="input"] [role="button"],
        [data-testid="stTextInput"] [role="button"] {
            background: transparent !important;
            background-color: transparent !important;
            border: 0 !important;
            border-left: 0 !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            outline: none !important;
            color: #FFF7CC !important;
        }

        div[data-baseweb="input"] button:focus,
        div[data-baseweb="input"] button:focus-visible,
        [data-testid="stTextInput"] button:focus,
        [data-testid="stTextInput"] button:focus-visible,
        div[data-baseweb="input"] [role="button"]:focus,
        div[data-baseweb="input"] [role="button"]:focus-visible,
        [data-testid="stTextInput"] [role="button"]:focus,
        [data-testid="stTextInput"] [role="button"]:focus-visible {
            border: 0 !important;
            outline: none !important;
            box-shadow: none !important;
        }

        /* Suppress Streamlit/BaseWeb validation rings without adding a second outline. */
        div[data-baseweb="input"] > div[aria-invalid="true"],
        div[data-baseweb="input"][aria-invalid="true"] > div,
        div[data-baseweb="input"] > div[data-invalid="true"],
        div[data-baseweb="input"][data-invalid="true"] > div,
        [data-testid="stTextInput"] [aria-invalid="true"],
        [data-testid="stDateInput"] [aria-invalid="true"] {
            border-color: rgba(0, 212, 106, 0.18) !important;
            box-shadow: none !important;
            outline: none !important;
        }

        div[data-baseweb="input"] > div[aria-invalid="true"]:focus-within,
        div[data-baseweb="input"][aria-invalid="true"] > div:focus-within,
        div[data-baseweb="input"] > div[data-invalid="true"]:focus-within,
        div[data-baseweb="input"][data-invalid="true"] > div:focus-within,
        [data-testid="stTextInput"] [aria-invalid="true"]:focus-within,
        [data-testid="stDateInput"] [aria-invalid="true"]:focus-within {
            border-color: rgba(0, 212, 106, 0.88) !important;
            box-shadow: 0 0 0 1px rgba(0, 212, 106, 0.64) !important;
            outline: none !important;
        }

        div[data-baseweb="input"],
        div[data-baseweb="input"] *,
        [data-testid="stTextInput"],
        [data-testid="stTextInput"] *,
        [data-testid="stDateInput"],
        [data-testid="stDateInput"] * {
            --focus-color: #00D46A !important;
            --input-border-color: rgba(0, 212, 106, 0.18) !important;
            --error-color: #00D46A !important;
            outline-color: transparent !important;
        }

        div[data-baseweb="input"] svg,
        [data-testid="stTextInput"] svg {
            color: #FFF7CC !important;
        }

        [data-baseweb="tag"] {
            background: linear-gradient(135deg, rgba(0, 212, 106, 0.22), rgba(0, 168, 90, 0.14)) !important;
            border: 1px solid rgba(0, 212, 106, 0.38) !important;
            color: #FFF7CC !important;
            border-radius: 999px !important;
        }
        [data-baseweb="tag"] span { color: #FFF7CC !important; }
        [data-baseweb="tag"] svg { color: #FFF7CC !important; }

        .dashboard-hero {
            padding: 1.8rem 2rem;
            border: 1px solid var(--border);
            border-radius: 24px;
            background: __HERO_BACKGROUND__;
            box-shadow: __HERO_BOX_SHADOW__;
            backdrop-filter: __HERO_BACKDROP_FILTER__;
            margin-bottom: 1.4rem;
        }

        .eyebrow {
            color: var(--cyan);
            text-transform: uppercase;
            letter-spacing: 0.16em;
            font-size: 0.78rem;
            font-weight: 800;
            margin-bottom: 0.35rem;
        }

        .dashboard-title {
            font-size: clamp(2.2rem, 4vw, 4rem);
            line-height: 1.02;
            font-weight: 900;
            color: #FFFBEA;
            margin: 0;
            text-shadow: 0 3px 16px rgba(0,0,0,0.88);
        }

        .dashboard-subtitle {
            color: var(--text-soft);
            font-size: 1rem;
            margin-top: 0.8rem;
            text-shadow: 0 2px 10px rgba(0,0,0,0.82);
        }

        .section-title {
            font-size: 1.35rem;
            font-weight: 850;
            color: #FFFBEA;
            margin: 1.6rem 0 0.75rem 0;
        }

        div[data-testid="stMetric"] {
            position: relative;
            background: __METRIC_BACKGROUND__ !important;
            border: 1px solid rgba(0, 212, 106, 0.56) !important;
            border-radius: 20px !important;
            padding: 1.05rem 1.1rem !important;
            box-shadow: __METRIC_BOX_SHADOW__ !important;
            backdrop-filter: __METRIC_BACKDROP_FILTER__;
            min-height: 124px;
            overflow: hidden;
        }

        div[data-testid="stMetric"]::before {
            content: "";
            position: absolute;
            top: 0;
            left: 1rem;
            right: 1rem;
            height: 2px;
            background: linear-gradient(90deg, rgba(0,212,106,0), rgba(0,212,106,0.92), rgba(0,168,90,0));
        }

        div[data-testid="stMetricLabel"] p {
            color: #F5EFD8 !important;
            font-weight: 800 !important;
            font-size: 0.82rem !important;
            line-height: 1.25 !important;
            text-shadow: 0 2px 12px rgba(0,0,0,0.96), 0 0 18px rgba(0,0,0,0.70);
        }

        div[data-testid="stMetricValue"] {
            color: #FFFBEA !important;
            font-size: clamp(1.85rem, 2.2vw, 2.45rem) !important;
            line-height: 1 !important;
            font-weight: 950 !important;
            letter-spacing: 0 !important;
            text-shadow: 0 3px 18px rgba(0,0,0,0.98), 0 0 22px rgba(0,0,0,0.78);
            white-space: normal !important;
            overflow-wrap: anywhere !important;
        }

        div[data-testid="stDataFrame"] {
            border: 1px solid var(--border);
            border-radius: 18px;
            overflow: hidden;
            box-shadow: 0 14px 36px rgba(0,0,0,0.30);
        }

        button[data-baseweb="tab"] {
            color: #CFC6A5 !important;
            font-weight: 750 !important;
        }

        button[data-baseweb="tab"][aria-selected="true"] {
            color: #00D46A !important;
        }

        div[data-baseweb="tab-highlight"] {
            background-color: #00D46A !important;
        }

        .stDownloadButton button, .stButton button {
            border-radius: 14px !important;
            border: 1px solid rgba(0, 212, 106, 0.45) !important;
            background: linear-gradient(135deg, rgba(0, 212, 106, 0.98), rgba(0, 168, 90, 0.86)) !important;
            color: #121008 !important;
            font-weight: 850 !important;
        
            padding: 0.55rem 1rem !important;
            font-size: 0.95rem !important;
            min-height: 42px !important;
        }
        
        /* Sidebar refresh button spacing */
        section[data-testid="stSidebar"] .stButton button {
            margin-bottom: 0.85rem !important;
        }
        
        section[data-testid="stSidebar"] .stButton button {
            width: auto !important;
            min-width: 145px !important;
            max-width: 165px !important;
        
            padding: 0.45rem 0.85rem !important;
            font-size: 0.90rem !important;
            min-height: 38px !important;
        }


        /* Final unified input styling: one calm surface, one green focus line, no orange/red rings. */
        :root {
            --mn-input-bg: rgba(13, 13, 9, 0.90);
            --mn-input-border: rgba(0, 212, 106, 0.18);
            --mn-input-border-hover: rgba(0, 212, 106, 0.28);
            --mn-input-border-focus: rgba(0, 212, 106, 0.92);
        }

        /* Put the single visible border on the BaseWeb input shell. */
        div[data-baseweb="input"],
        [data-testid="stTextInput"] div[data-baseweb="input"],
        [data-testid="stDateInput"] div[data-baseweb="input"],
        [data-testid="stNumberInput"] div[data-baseweb="input"] {
            background: var(--mn-input-bg) !important;
            background-color: var(--mn-input-bg) !important;
            border: 1px solid var(--mn-input-border) !important;
            border-radius: 14px !important;
            box-shadow: none !important;
            outline: none !important;
            overflow: hidden !important;
            transition: border-color 140ms ease, background-color 140ms ease !important;
        }

        div[data-baseweb="input"]:hover,
        [data-testid="stTextInput"] div[data-baseweb="input"]:hover,
        [data-testid="stDateInput"] div[data-baseweb="input"]:hover,
        [data-testid="stNumberInput"] div[data-baseweb="input"]:hover {
            border-color: var(--mn-input-border-hover) !important;
            box-shadow: none !important;
        }

        div[data-baseweb="input"]:focus-within,
        div[data-baseweb="input"]:has(input:focus),
        div[data-baseweb="input"]:has(input:focus-visible),
        [data-testid="stTextInput"] div[data-baseweb="input"]:focus-within,
        [data-testid="stDateInput"] div[data-baseweb="input"]:focus-within,
        [data-testid="stNumberInput"] div[data-baseweb="input"]:focus-within {
            border-color: var(--mn-input-border-focus) !important;
            box-shadow: none !important;
            outline: none !important;
        }

        /* Remove every inner rectangle so the field reads as one homogeneous tab. */
        div[data-baseweb="input"] > div,
        div[data-baseweb="input"] > div > div,
        div[data-baseweb="input"] > div > div > div,
        div[data-baseweb="input"] [data-baseweb="base-input"],
        div[data-baseweb="input"] [data-testid="stBaseInput"],
        [data-testid="stTextInput"] div[data-baseweb="input"] > div,
        [data-testid="stDateInput"] div[data-baseweb="input"] > div,
        [data-testid="stNumberInput"] div[data-baseweb="input"] > div {
            background: transparent !important;
            background-color: transparent !important;
            border: 0 !important;
            border-color: transparent !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            outline: none !important;
        }

        div[data-baseweb="input"] input,
        div[data-baseweb="input"] input:hover,
        div[data-baseweb="input"] input:focus,
        div[data-baseweb="input"] input:focus-visible,
        div[data-baseweb="input"] input:invalid,
        div[data-baseweb="input"] input:user-invalid,
        [data-testid="stTextInput"] input,
        [data-testid="stDateInput"] input,
        [data-testid="stNumberInput"] input {
            background: transparent !important;
            background-color: transparent !important;
            border: 0 !important;
            border-color: transparent !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            outline: none !important;
            caret-color: #00D46A !important;
        }

        /* Make the password eye area the same surface as the input; no black patch and no separate outline. */
        div[data-baseweb="input"] button,
        div[data-baseweb="input"] button:hover,
        div[data-baseweb="input"] button:focus,
        div[data-baseweb="input"] button:focus-visible,
        div[data-baseweb="input"] [role="button"],
        div[data-baseweb="input"] [role="button"]:hover,
        div[data-baseweb="input"] [role="button"]:focus,
        div[data-baseweb="input"] [role="button"]:focus-visible,
        div[data-baseweb="input"] svg,
        [data-testid="stTextInput"] button,
        [data-testid="stTextInput"] [role="button"] {
            background: transparent !important;
            background-color: transparent !important;
            border: 0 !important;
            border-left: 0 !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            outline: none !important;
            color: #FFF7CC !important;
        }

        /* Streamlit/BaseWeb invalid states sometimes inject orange/red borders; force them back to theme. */
        div[data-baseweb="input"][aria-invalid="true"],
        div[data-baseweb="input"][data-invalid="true"],
        div[data-baseweb="input"]:has(input[aria-invalid="true"]),
        div[data-baseweb="input"]:has(input:invalid),
        [data-testid="stTextInput"] div[aria-invalid="true"],
        [data-testid="stDateInput"] div[aria-invalid="true"],
        [data-testid="stNumberInput"] div[aria-invalid="true"] {
            border-color: var(--mn-input-border) !important;
            box-shadow: none !important;
            outline: none !important;
        }

        div[data-baseweb="input"][aria-invalid="true"]:focus-within,
        div[data-baseweb="input"][data-invalid="true"]:focus-within,
        div[data-baseweb="input"]:has(input[aria-invalid="true"]:focus),
        div[data-baseweb="input"]:has(input:invalid:focus),
        [data-testid="stTextInput"] div[aria-invalid="true"]:focus-within,
        [data-testid="stDateInput"] div[aria-invalid="true"]:focus-within,
        [data-testid="stNumberInput"] div[aria-invalid="true"]:focus-within {
            border-color: var(--mn-input-border-focus) !important;
            box-shadow: none !important;
            outline: none !important;
        }
        /* Timeline slider: make track, selected range, handles, and date labels green */
        div[data-testid="stSlider"] div[data-baseweb="slider"] > div {
            color: #00D46A !important;
        }
        
        div[data-testid="stSlider"] [role="slider"] {
            background-color: #00D46A !important;
            border-color: #00D46A !important;
            box-shadow: 0 0 0 2px rgba(0, 212, 106, 0.35) !important;
        }
        
        div[data-testid="stSlider"] [data-testid="stTickBar"] {
            color: #00D46A !important;
        }
        
        div[data-testid="stSlider"] div {
            accent-color: #00D46A !important;
        }
        
        .api-load-caption {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            margin: -0.35rem 0 1.05rem 0;
            padding: 0.38rem 0.72rem;
            border: 1px solid rgba(0, 212, 106, 0.22);
            border-radius: 999px;
            background: rgba(13, 13, 9, 0.46);
            color: #B8B29F;
            font-size: 0.80rem;
            font-weight: 650;
            backdrop-filter: blur(6px);
        }

        .api-load-caption span {
            color: #FFF7CC;
            font-weight: 800;
        }
</style>
        """
        .replace("__BACKGROUND_IMAGE_LAYER__", background_image_layer)
        .replace("__HERO_BACKGROUND__", hero_background)
        .replace("__HERO_BACKDROP_FILTER__", hero_backdrop_filter)
        .replace("__HERO_BOX_SHADOW__", hero_box_shadow)
        .replace("__METRIC_BACKGROUND__", metric_background)
        .replace("__METRIC_BACKDROP_FILTER__", metric_backdrop_filter)
        .replace("__METRIC_BOX_SHADOW__", metric_box_shadow),
        unsafe_allow_html=True,
    )

def dashboard_background_image_layer(image_url: str) -> str:
    if not image_url:
        return ""

    safe_url = image_url.replace("\\", "\\\\").replace("'", "\\'")
    return (
        "linear-gradient(rgba(5, 5, 5, 0.78), rgba(5, 5, 5, 0.88)),\n"
        f"                url('{safe_url}'),\n"
    )

def dashboard_hero_background(*, has_background_image: bool) -> str:
    if has_background_image:
        return "transparent"

    return (
        "linear-gradient(135deg, rgba(20, 18, 10, 0.98), rgba(5, 5, 5, 0.82)), "
        "linear-gradient(90deg, rgba(0, 212, 106, 0.12), transparent)"
    )


def dashboard_hero_backdrop_filter(*, has_background_image: bool) -> str:
    return "none" if has_background_image else "blur(12px)"


def dashboard_hero_box_shadow(*, has_background_image: bool) -> str:
    if has_background_image:
        return "inset 0 1px 0 rgba(0,212,106,0.20)"

    return "0 24px 70px rgba(0,0,0,0.38), inset 0 1px 0 rgba(0,212,106,0.18)"


def dashboard_metric_background(*, has_background_image: bool) -> str:
    if has_background_image:
        return "transparent"

    return (
        "linear-gradient(135deg, rgba(0, 212, 106, 0.12), rgba(0, 168, 90, 0.04) 42%, rgba(5, 5, 5, 0.94)), "
        "linear-gradient(180deg, rgba(28, 25, 14, 0.98), rgba(8, 8, 5, 0.98))"
    )


def dashboard_metric_backdrop_filter(*, has_background_image: bool) -> str:
    return "none" if has_background_image else "blur(10px)"


def dashboard_metric_box_shadow(*, has_background_image: bool) -> str:
    if has_background_image:
        return "inset 0 1px 0 rgba(0,212,106,0.22)"

    return (
        "0 18px 42px rgba(0,0,0,0.42), "
        "0 0 28px rgba(0,168,90,0.08), "
        "inset 0 1px 0 rgba(0,212,106,0.18)"
    )


def dashboard_background_image_url() -> str:
    source = read_secret("DASHBOARD_BACKGROUND_IMAGE")
    if source and re.match(r"^(https?://|data:)", source, flags=re.IGNORECASE):
        return source

    image_path = Path(source).expanduser() if source else DEFAULT_BACKGROUND_IMAGE
    if not image_path.is_absolute():
        image_path = APP_DIR / image_path
    if source and not image_path.is_file():
        image_path = DEFAULT_BACKGROUND_IMAGE


    if not image_path.is_file():
        return ""

    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    encoded_image = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded_image}"


def render_header(selected_group: str, selected_vessels: list[str]) -> None:
    vessel_text = "All selected vessels" if len(selected_vessels) != 1 else selected_vessels[0]
    st.markdown(
        f"""
        <div class="dashboard-hero">
            <div class="eyebrow">Marorka metrics monitoring</div>
            <h1 class="dashboard-title">ESG - metrics</h1>
            <div class="dashboard-subtitle">
                {escape(selected_group)} | {escape(vessel_text)}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_api_load_caption(metadata: dict[str, Any] | None) -> None:
    metadata = metadata or {}
    last_load = metadata.get("loaded_at_local") or metadata.get("loaded_at_utc") or "-"

    last_load_display = str(last_load).replace(" EEST", "").replace(" EET", "")

    st.markdown(
        f"""
        <div class="api-load-caption">
            Last API load: <span>{escape(last_load_display)} LT</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =============================================================================
# Secrets/auth/API helpers
# =============================================================================


class MarorkaConfigError(RuntimeError):
    pass


def read_secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, os.getenv(name, default))
    except Exception:
        value = os.getenv(name, default)
    return str(value).strip() if value is not None else default


def app_timezone() -> ZoneInfo:
    """Return dashboard display timezone. Defaults to Greece local time."""
    timezone_name = read_secret("APP_TIMEZONE", "Europe/Athens")
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return ZoneInfo("Europe/Athens")


def local_time_label(dt_utc: datetime | None = None) -> str:
    """Format a UTC timestamp in the configured dashboard timezone."""
    dt_utc = dt_utc or datetime.now(timezone.utc)
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    local_dt = dt_utc.astimezone(app_timezone())
    return local_dt.strftime("%d-%m-%Y %H:%M:%S %Z")


def get_query_param(name: str, default: str = "") -> str:
    """Read one query parameter value, compatible with newer and older Streamlit versions."""
    try:
        value = st.query_params.get(name, default)
    except Exception:
        try:
            value = st.experimental_get_query_params().get(name, [default])
        except Exception:
            value = default

    if isinstance(value, list):
        value = value[0] if value else default

    return str(value) if value is not None else default


def is_warmup_request() -> bool:
    return get_query_param("warmup", "0") == "1"


def warmup_token_is_valid() -> bool:
    expected_token = read_secret("WARMUP_TOKEN")
    provided_token = get_query_param("token", "")

    if not expected_token:
        return False

    return hmac.compare_digest(provided_token, expected_token)


def run_warmup_if_requested() -> None:
    """Warm up the ESG API/cache via a secret-token URL before password protection."""
    if not is_warmup_request():
        return
        
    apply_custom_css()
    
    if not warmup_token_is_valid():
        st.error("Invalid or missing warmup token.")
        st.stop()

    username = read_secret("MARORKA_USERNAME")
    password = read_secret("MARORKA_PASSWORD")
    token = read_secret("MARORKA_TOKEN")
    auth_method = read_secret("MARORKA_AUTH_METHOD", "basic")

    if auth_method.lower() in {"basic", "digest"} and (not username or not password):
        st.error("Warmup failed: MARORKA_USERNAME and MARORKA_PASSWORD are required.")
        st.stop()

    if get_query_param("force", "0") == "1":
        fetch_report_data.clear()
        cached_transform_report_data.clear()

    try:
        with st.spinner("Warming up API..."):
            raw_df, metadata = fetch_report_data(
                username=username,
                password=password,
                token=token,
                auth_method=auth_method,
                start_date=API_FULL_START_DATE,
            )
            df = cached_transform_report_data(raw_df)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        st.error(f"Warmup failed: Marorka API request failed with status {status}.")
        st.stop()
    except (MarorkaConfigError, ValueError, requests.RequestException) as exc:
        st.error(f"Warmup failed: {exc}")
        st.stop()

    st.success("Warmup OK.")
    st.write(
        {
            "api_rows": int(len(raw_df)),
            "dashboard_rows": int(len(df)),
            "force_refresh": get_query_param("force", "0") == "1",
            "last_api_load_local": metadata.get("loaded_at_local"),
            "kept_rows": metadata.get("kept_rows", metadata.get("rows", 0)),
            "scanned_rows": metadata.get("scanned_rows", 0),
            "pages": metadata.get("pages", 0),
            "fetch_seconds": metadata.get("fetch_seconds", "-"),
        }
    )
    st.stop()


def require_dashboard_password() -> None:
    dashboard_password = read_secret("DASHBOARD_PASSWORD")
    if not dashboard_password:
        return

    if st.session_state.get("dashboard_authenticated"):
        return

    apply_custom_css()
    st.markdown(
        """
        <div class="dashboard-hero">
            <div class="eyebrow">Secure access</div>
            <h1 class="dashboard-title">ESG - metrics</h1>
            <div class="dashboard-subtitle">Enter your dashboard password to continue.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    entered_password = st.text_input("Password", type="password")

    if st.button("Sign in", type="primary"):
        if hmac.compare_digest(entered_password, dashboard_password):
            st.session_state["dashboard_authenticated"] = True
            st.rerun()
        st.error("Invalid password.")

    st.stop()


def request_auth(username: str, password: str, auth_method: str) -> Any:
    method = auth_method.lower()
    if method == "basic":
        return HTTPBasicAuth(username, password)
    if method == "digest":
        return HTTPDigestAuth(username, password)
    if method == "bearer":
        return None
    if method in {"none", "anonymous", ""}:
        return None
    raise MarorkaConfigError("Unsupported MARORKA_AUTH_METHOD. Use basic, digest, bearer, or none.")


def request_headers(token: str, auth_method: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if auth_method.lower() == "bearer":
        if not token:
            raise MarorkaConfigError("MARORKA_TOKEN is required for bearer auth.")
        headers["Authorization"] = f"Bearer {token}"
    return headers


def default_report_window(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()

    start_month = today.month - 2
    start_year = today.year
    while start_month <= 0:
        start_month += 12
        start_year -= 1

    start_date = date(start_year, start_month, 1)

    if today.month == 12:
        end_date = date(today.year, 12, 31)
    else:
        end_date = date(today.year, today.month + 1, 1) - timedelta(days=1)

    return start_date, end_date


def build_odata_url(start_date: date) -> str:
    start_text = start_date.strftime("%Y-%m-%d")
    params = {
        "$filter": f"StartDateTimeGMT gt DateTime'{start_text}'",
        "$select": ",".join(SOURCE_COLUMNS),
    }
    return f"{ODATA_ENDPOINT}?{urlencode(params)}"


def extract_odata_page(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(payload, list):
        return payload, None

    if not isinstance(payload, dict):
        raise ValueError("Could not parse OData response payload.")

    rows = payload.get("value")
    next_link = payload.get("@odata.nextLink") or payload.get("odata.nextLink")

    if rows is None and isinstance(payload.get("d"), dict):
        data = payload["d"]
        rows = data.get("results")
        next_link = next_link or data.get("__next")

    if rows is None:
        raise ValueError("Could not find OData rows in the API response.")

    return rows, next_link


def rows_to_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if "__metadata" in df.columns:
        df = df.drop(columns=["__metadata"])
    for column in SOURCE_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA
    return df[SOURCE_COLUMNS]


def compact_odata_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted_keys = wanted_value_keys()
    compact_rows: list[dict[str, Any]] = []

    for row in rows:
        value_description = row.get("ValueDescription")
        if value_description is None:
            continue
        if normalize_text(value_description) not in wanted_keys:
            continue
        if row.get("ReportType") in EXCLUDED_REPORT_TYPES:
            continue
        compact_rows.append({column: row.get(column) for column in SOURCE_COLUMNS})

    return compact_rows

@st.cache_data(ttl=API_CACHE_TTL_SECONDS, show_spinner=False)
def fetch_report_data(
    username: str,
    password: str,
    token: str,
    auth_method: str,
    start_date: date,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    started_at = time.perf_counter()
    next_url = build_odata_url(start_date)
    kept_rows: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    pages = 0
    total_bytes = 0
    scanned_rows = 0
    first_url = next_url
    auth = request_auth(username, password, auth_method)
    headers = request_headers(token, auth_method)

    with requests.Session() as session:
        session.headers.update(headers)
        for _ in range(MAX_ODATA_PAGES):
            if next_url in seen_urls:
                break
            seen_urls.add(next_url)

            response = session.get(
                next_url,
                auth=auth,
                timeout=90,
            )
            total_bytes += len(response.content)
            response.raise_for_status()
            pages += 1

            page_rows, next_link = extract_odata_page(response.json())
            scanned_rows += len(page_rows)
            kept_rows.extend(compact_odata_rows(page_rows))

            if not next_link:
                break
            next_url = urljoin(next_url, next_link)

    loaded_at_utc = datetime.now(timezone.utc)
    metadata = {
        "loaded_at_utc": loaded_at_utc.strftime("%d-%m-%Y %H:%M:%S UTC"),
        "loaded_at_local": local_time_label(loaded_at_utc),
        "rows": len(kept_rows),
        "kept_rows": len(kept_rows),
        "scanned_rows": scanned_rows,
        "discarded_rows": max(scanned_rows - len(kept_rows), 0),
        "pages": pages,
        "downloaded_mb": round(total_bytes / 1024 / 1024, 2),
        "fetch_seconds": round(time.perf_counter() - started_at, 2),
        "first_url": first_url,
        "hit_page_limit": pages >= MAX_ODATA_PAGES,
    }
    return rows_to_dataframe(kept_rows), metadata


# =============================================================================
# Transform helpers
# =============================================================================


def normalize_text(value: Any) -> str:
    text = str(value).lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def wanted_value_keys() -> set[str]:
    return {normalize_text(alias) for aliases in VALUE_ALIASES.values() for alias in aliases}


def parse_datetime_series(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce", utc=True)
    missing_mask = parsed.isna()

    if missing_mask.any():
        date_text = series.astype("string")
        dotnet_millis = date_text.str.extract(r"/Date\((-?\d+)").iloc[:, 0]
        dotnet_parsed = pd.to_datetime(
            pd.to_numeric(dotnet_millis, errors="coerce"),
            errors="coerce",
            unit="ms",
            utc=True,
        )
        parsed = parsed.mask(missing_mask, dotnet_parsed)

    return parsed


def parse_numeric_value(value: Any) -> Any:
    if pd.isna(value):
        return pd.NA

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return pd.NA

    duration_match = re.fullmatch(r"(-?\d+):([0-5]?\d)(?::([0-5]?\d))?", text)
    if duration_match:
        hours = int(duration_match.group(1))
        sign = -1 if hours < 0 else 1
        minutes = int(duration_match.group(2))
        seconds = int(duration_match.group(3) or 0)
        return sign * (abs(hours) + minutes / 60 + seconds / 3600)

    numeric_text = text.replace(" ", "")
    if re.fullmatch(r"-?\d+,\d+", numeric_text):
        numeric_text = numeric_text.replace(",", ".")
    else:
        numeric_text = numeric_text.replace(",", "")

    numeric_text = re.sub(r"[^0-9.\-]", "", numeric_text)
    if numeric_text in {"", "-", ".", "-."}:
        return pd.NA

    try:
        return float(numeric_text)
    except ValueError:
        return pd.NA


def parse_numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.map(parse_numeric_value), errors="coerce")


def first_non_null(series: pd.Series) -> Any:
    values = series.dropna()
    if values.empty:
        return pd.NA
    return values.iloc[0]


def last_non_null(series: pd.Series) -> Any:
    values = series.dropna()
    if values.empty:
        return pd.NA
    return values.iloc[-1]


def match_selected_vessels(raw_ship_names: pd.Series, selected_vessels: list[str]) -> pd.Series:
    selected_keys = {normalize_text(vessel) for vessel in selected_vessels}
    return raw_ship_names.map(normalize_text).isin(selected_keys)


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numerator = pd.to_numeric(numerator, errors="coerce")
    denominator = pd.to_numeric(denominator, errors="coerce")
    denominator = denominator.mask(denominator == 0)
    return numerator / denominator


def sum_numeric_columns(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    available_columns = [column for column in columns if column in df.columns]
    if not available_columns:
        return pd.Series(pd.NA, index=df.index, dtype="Float64")
    return df[available_columns].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1)


def build_report_rows(df: pd.DataFrame) -> pd.DataFrame:
    group_keys = ["ReportId", "ShipName", "EndDateTimeGMT"]
    available_group_keys = [key for key in group_keys if key in df.columns]
    if not available_group_keys:
        available_group_keys = ["ShipName", "EndDateTimeGMT"]

    sorted_df = df.sort_values("_source_order")
    base_columns = [
        column
        for column in ["ReportType", "StartDateTimeGMT", "LapTime", "StateName"]
        if column in sorted_df.columns
    ]

    report_df = (
        sorted_df
        .groupby(available_group_keys, sort=False, dropna=False)[base_columns]
        .agg(last_non_null)
        .reset_index()
    )

    alias_to_column = {
        normalize_text(alias): column
        for column, aliases in VALUE_ALIASES.items()
        for alias in aliases
    }
    value_rows = sorted_df.loc[
        sorted_df["_value_key"].isin(alias_to_column) & sorted_df["ParsedValue"].notna(),
        [*available_group_keys, "_value_key", "_source_order", "ParsedValue"],
    ].copy()

    if not value_rows.empty:
        value_rows["_canonical_column"] = value_rows["_value_key"].map(alias_to_column)
        value_rows = value_rows.drop_duplicates(
            [*available_group_keys, "_canonical_column"],
            keep="last",
        )
        value_table = (
            value_rows
            .pivot(index=available_group_keys, columns="_canonical_column", values="ParsedValue")
            .reset_index()
        )
        report_df = report_df.merge(value_table, on=available_group_keys, how="left")

    for column in VALUE_ALIASES:
        if column not in report_df.columns:
            report_df[column] = pd.NA

    return report_df


def transform_report_data(raw_df: pd.DataFrame) -> pd.DataFrame:
    missing_columns = sorted(set(SOURCE_COLUMNS).difference(raw_df.columns))
    if missing_columns:
        raise ValueError(f"Missing expected API columns: {', '.join(missing_columns)}")

    df = raw_df.copy()
    df["StartDateTimeGMT"] = parse_datetime_series(df["StartDateTimeGMT"])
    df["EndDateTimeGMT"] = parse_datetime_series(df["EndDateTimeGMT"])
    df["LapTime"] = parse_numeric_series(df["LapTime"])
    df["ParsedValue"] = parse_numeric_series(df["ReportedValue"])
    df["_value_key"] = df["ValueDescription"].map(normalize_text)
    df["_source_order"] = range(len(df))

    df = df[
        df["ValueDescription"].notna()
        & df["_value_key"].isin(wanted_value_keys())
        & ~df["ReportType"].isin(EXCLUDED_REPORT_TYPES)
    ].copy()

    if df.empty:
        return pd.DataFrame(columns=DISPLAY_COLUMNS)

    report_df = build_report_rows(df)
    if report_df.empty:
        return pd.DataFrame(columns=DISPLAY_COLUMNS)

    report_df = report_df.sort_values(["ShipName", "EndDateTimeGMT", "ReportId"], na_position="last")
    report_df = add_calculations(report_df)
    return report_df




@st.cache_data(ttl=API_CACHE_TTL_SECONDS, show_spinner=False)
def cached_transform_report_data(raw_df: pd.DataFrame) -> pd.DataFrame:
    return transform_report_data(raw_df)


def filter_reports_for_selection(
    report_df: pd.DataFrame,
    selected_vessels: list[str],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    if report_df.empty:
        return report_df

    filtered = report_df.copy()
    start_timestamp = pd.Timestamp(start_date, tz="UTC")
    end_timestamp = pd.Timestamp(end_date + timedelta(days=1), tz="UTC")
    start_values = pd.to_datetime(filtered["StartDateTimeGMT"], errors="coerce", utc=True)

    filtered = filtered[
        match_selected_vessels(filtered["ShipName"], selected_vessels)
        & start_values.ge(start_timestamp)
        & start_values.lt(end_timestamp)
    ].copy()
    return filtered


def add_calculations(report_df: pd.DataFrame) -> pd.DataFrame:
    """Keep the same transform hook as the performance .

    This twin  does not calculate performance KPIs. It only pivots the
    selected Marorka ValueDescription rows into colleague-facing columns and
    rounds numeric water/waste quantities.
    """
    df = report_df.copy()
    for column in WATER_WASTE_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").round(3)
    return df


# =============================================================================
# Display/export helpers
# =============================================================================


def format_value(value: Any, decimals: int = 2, suffix: str = "") -> str:
    if pd.isna(value):
        return "-"
    if isinstance(value, str):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,.{decimals}f}{suffix}"


def format_percentage(value: Any) -> str:
    if pd.isna(value):
        return "-"
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "-"


def format_datetime(value: Any) -> str:
    if pd.isna(value):
        return "-"
    return pd.Timestamp(value).strftime(DISPLAY_DATETIME_FORMAT)


def make_display_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    columns = [column for column in DISPLAY_COLUMNS if column in df.columns]
    display_df = df[columns].copy()
    for column in ["StartDateTimeGMT", "EndDateTimeGMT"]:
        if column in display_df.columns:
            display_df[column] = pd.to_datetime(display_df[column], errors="coerce").dt.strftime(DISPLAY_DATETIME_FORMAT)
    numeric_columns = [
        column for column in display_df.columns
        if column not in {"ReportType", "ShipName", "StateName", "StartDateTimeGMT", "EndDateTimeGMT"}
    ]
    for column in numeric_columns:
        values = pd.to_numeric(display_df[column], errors="coerce")
        display_df[column] = values.map(lambda value: "-" if pd.isna(value) else f"{value:,.3f}")
    return display_df.fillna("-")


@st.cache_data(show_spinner=False)
def to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    safe_df = df.copy()
    for column in safe_df.columns:
        if pd.api.types.is_datetime64_any_dtype(safe_df[column]):
            safe_df[column] = pd.to_datetime(safe_df[column], errors="coerce").dt.tz_localize(None)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        safe_df.to_excel(writer, index=False, sheet_name="metrics")
        worksheet = writer.sheets["metrics"]
        for column_cells in worksheet.columns:
            max_length = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 45)
    return output.getvalue()


def numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(df[column], errors="coerce")


def render_kpis(report_df: pd.DataFrame) -> None:
    totals = {
        column: numeric_series(report_df, column).sum(min_count=1)
        for column in WATER_WASTE_COLUMNS
    }

    cols = st.columns(len(WATER_WASTE_COLUMNS))
    for column, metric_column in zip(WATER_WASTE_COLUMNS, cols):
        metric_column.metric(column, format_value(totals.get(column), 2))



def latest_by_vessel(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "ShipName" not in df.columns:
        return df
    return df.sort_values("EndDateTimeGMT").groupby("ShipName", as_index=False, dropna=False).tail(1).sort_values("ShipName")


# =============================================================================
# Excel-like report filters
# =============================================================================


def unique_display_values(series: pd.Series, limit: int = 500) -> list[str]:
    values = series.astype("string").fillna("(Blank)").drop_duplicates().tolist()
    values = sorted(values, key=lambda value: value.casefold())
    return values[:limit]


def parse_optional_float(value: str) -> tuple[float | None, bool]:
    text = str(value or "").strip()
    if not text:
        return None, True
    normalized = text.replace(" ", "").replace(",", "")
    try:
        return float(normalized), True
    except ValueError:
        return None, False


def parse_optional_date(value: str) -> tuple[pd.Timestamp | None, bool]:
    text = str(value or "").strip()
    if not text:
        return None, True
    parsed = pd.to_datetime(text, dayfirst=True, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None, False
    return parsed, True


def filterable_columns(df: pd.DataFrame) -> list[str]:
    preferred = [column for column in DISPLAY_COLUMNS if column in df.columns]
    remaining = [column for column in df.columns if column not in preferred]
    return preferred + remaining


def is_numeric_like(series: pd.Series) -> bool:
    values = pd.to_numeric(series, errors="coerce")
    return values.notna().any()


def filter_digest(column: str) -> str:
    return sha256(column.encode("utf-8")).hexdigest()[:10]


def seed_filter_defaults(
    *,
    key_prefix: str,
    default_columns: list[str] | None = None,
    default_numeric_filters: dict[str, dict[str, str]] | None = None,
    default_categorical_filters: dict[str, list[str]] | None = None,
) -> None:
    selected_key = f"{key_prefix}_columns"
    if selected_key not in st.session_state and default_columns:
        st.session_state[selected_key] = list(default_columns)

    for column, bounds in (default_numeric_filters or {}).items():
        digest = filter_digest(column)
        min_key = f"{key_prefix}_{digest}_min"
        max_key = f"{key_prefix}_{digest}_max"
        if min_key not in st.session_state:
            st.session_state[min_key] = bounds.get("min", "")
        if max_key not in st.session_state:
            st.session_state[max_key] = bounds.get("max", "")

    for column, values in (default_categorical_filters or {}).items():
        value_key = f"{key_prefix}_{filter_digest(column)}_values"
        if value_key not in st.session_state:
            st.session_state[value_key] = list(values)


def render_excel_like_filters(
    df: pd.DataFrame,
    *,
    key_prefix: str,
    label: str,
    default_columns: list[str] | None = None,
    default_numeric_filters: dict[str, dict[str, str]] | None = None,
    default_categorical_filters: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    seed_filter_defaults(
        key_prefix=key_prefix,
        default_columns=default_columns,
        default_numeric_filters=default_numeric_filters,
        default_categorical_filters=default_categorical_filters,
    )

    current_options = filterable_columns(df)
    selected_key = f"{key_prefix}_columns"
    previous_columns = st.session_state.get(selected_key, [])
    if not isinstance(previous_columns, list):
        previous_columns = []

    # Keep previously chosen filters available even if the current vessel/date
    # selection has fewer columns. This makes the filter setup stable across
    # reruns, vessel changes, and date-window changes.
    options = []
    for column in [*previous_columns, *current_options]:
        if column not in options:
            options.append(column)

    selected_columns = st.multiselect(
        label,
        options=options,
        key=selected_key,
        help="Choose columns to filter. Numeric columns use Min/Max text boxes; text columns use value selection.",
    )

    specs: list[dict[str, Any]] = []
    for column in selected_columns:
        if column not in df.columns:
            st.caption(f"{column}: retained, but not present in the currently loaded data.")
            continue

        st.caption(f"Filter: {column}")
        series = df[column]

        if pd.api.types.is_datetime64_any_dtype(series):
            digest = filter_digest(column)
            from_key = f"{key_prefix}_{digest}_from"
            to_key = f"{key_prefix}_{digest}_to"
            left, right = st.columns(2)
            from_text = left.text_input("From", key=from_key, placeholder="dd/mm/yyyy")
            to_text = right.text_input("To", key=to_key, placeholder="dd/mm/yyyy")
            from_value, from_ok = parse_optional_date(from_text)
            to_value, to_ok = parse_optional_date(to_text)
            if not from_ok or not to_ok:
                st.warning(f"{column}: enter dates as dd/mm/yyyy or yyyy-mm-dd.")
            specs.append({"column": column, "kind": "datetime", "from": from_value, "to": to_value})
            continue

        if is_numeric_like(series):
            values = pd.to_numeric(series, errors="coerce").dropna()
            if not values.empty:
                st.caption(f"Loaded range: {format_value(values.min(), 3)} to {format_value(values.max(), 3)}")
            digest = filter_digest(column)
            min_key = f"{key_prefix}_{digest}_min"
            max_key = f"{key_prefix}_{digest}_max"
            default_rule = (default_numeric_filters or {}).get(column, {})
            min_op = default_rule.get("min_op", ">=")
            max_op = default_rule.get("max_op", "<=")
            left, right = st.columns(2)
            min_text = left.text_input("Min", key=min_key, placeholder="no minimum")
            max_text = right.text_input("Max", key=max_key, placeholder="no maximum")
            minimum, min_ok = parse_optional_float(min_text)
            maximum, max_ok = parse_optional_float(max_text)
            if not min_ok or not max_ok:
                st.warning(f"{column}: enter numeric Min/Max values only.")
            if minimum is not None and maximum is not None and minimum > maximum:
                minimum, maximum = maximum, minimum
                min_op, max_op = ">=", "<="
            specs.append({
                "column": column,
                "kind": "numeric",
                "min": minimum,
                "max": maximum,
                "min_op": min_op,
                "max_op": max_op,
            })
            continue

        value_key = f"{key_prefix}_{filter_digest(column)}_values"
        previous_values = st.session_state.get(value_key, [])
        if not isinstance(previous_values, list):
            previous_values = []
        value_options = []
        for value in [*previous_values, *unique_display_values(series)]:
            if value not in value_options:
                value_options.append(value)
        selected_values = st.multiselect(
            "Values",
            options=value_options,
            key=value_key,
            help="Leave blank to include all values for this column.",
        )
        specs.append({"column": column, "kind": "categorical", "values": selected_values})

    return specs


def apply_excel_like_filters(df: pd.DataFrame, specs: list[dict[str, Any]]) -> pd.DataFrame:
    filtered = df.copy()

    for spec in specs:
        column = spec.get("column")
        if column not in filtered.columns:
            continue

        kind = spec.get("kind")
        if kind == "numeric":
            values = pd.to_numeric(filtered[column], errors="coerce")
            minimum = spec.get("min")
            maximum = spec.get("max")
            min_op = spec.get("min_op", ">=")
            max_op = spec.get("max_op", "<=")
            if minimum is not None:
                if min_op == ">":
                    filtered = filtered[values > minimum]
                else:
                    filtered = filtered[values >= minimum]
                values = pd.to_numeric(filtered[column], errors="coerce")
            if maximum is not None:
                if max_op == "<":
                    filtered = filtered[values < maximum]
                else:
                    filtered = filtered[values <= maximum]

        elif kind == "datetime":
            values = pd.to_datetime(filtered[column], errors="coerce", utc=True)
            from_value = spec.get("from")
            to_value = spec.get("to")
            if from_value is not None:
                filtered = filtered[values >= from_value]
                values = pd.to_datetime(filtered[column], errors="coerce", utc=True)
            if to_value is not None:
                # Include the full selected day.
                filtered = filtered[values < (to_value + pd.Timedelta(days=1))]

        elif kind == "categorical":
            selected_values = spec.get("values") or []
            if selected_values:
                values = filtered[column].astype("string").fillna("(Blank)")
                filtered = filtered[values.isin(selected_values)]

    return filtered


# =============================================================================
# Sidebar
# =============================================================================


def selected_vessel_controls() -> tuple[str, list[str]]:
    group_options = ["Single vessel", "All fleets"] + list(VESSEL_GROUPS.keys())
    selected_group = st.sidebar.selectbox("Fleet group", options=group_options)

    if selected_group == "Single vessel":
        vessel = st.sidebar.selectbox("Vessel to include", options=VESSEL_OPTIONS)
        return selected_group, [vessel]

    if selected_group == "All fleets":
        group_vessels = VESSEL_OPTIONS
    else:
        group_vessels = VESSEL_GROUPS[selected_group]

    vessels = st.sidebar.multiselect(
        "Vessels to include",
        options=group_vessels,
        default=group_vessels,
        help=(
            "This controls the dashboard display and KPI calculations only. "
            "The API data has already been loaded broadly for the selected date window."
        ),
    )

    if not vessels:
        st.sidebar.caption(
            "No vessels selected manually, so all vessels in this fleet group are included."
        )
        vessels = group_vessels

    return selected_group, vessels


def sidebar_controls() -> tuple[date, date, str, list[str], bool]:
    api_start_date = API_FULL_START_DATE
    api_end_date = date.today()

    refresh = st.sidebar.button("Refresh API data", use_container_width=False)

    group, vessels = selected_vessel_controls()

    return api_start_date, api_end_date, group, vessels, refresh





def render_dashboard_date_slicer(df: pd.DataFrame) -> tuple[pd.DataFrame, date, date]:
    if df.empty or "StartDateTimeGMT" not in df.columns:
        today = date.today()
        return df, today, today

    dates = pd.to_datetime(df["StartDateTimeGMT"], errors="coerce", utc=True).dt.date.dropna()
    if dates.empty:
        today = date.today()
        return df, today, today

    min_date = max(dates.min(), API_FULL_START_DATE)
    max_date = min(dates.max(), date.today())

    st.markdown('<div class="section-title">Report Period</div>', unsafe_allow_html=True)
    st.caption("Drag the handles to choose the time period used by the summary cards and dashboard tables.")

    if min_date >= max_date:
        st.caption(f"Available data period: {min_date.strftime('%d/%m/%Y')}")
        selected_start, selected_end = min_date, max_date
    else:
        selected_start, selected_end = st.slider(
            "Timeline slicer",
            min_value=min_date,
            max_value=max_date,
            value=(min_date, max_date),
            format="DD/MM/YYYY",
            key="dashboard_timeline_slicer",
            label_visibility="collapsed",
        )

    start_timestamp = pd.Timestamp(selected_start, tz="UTC")
    end_timestamp = pd.Timestamp(selected_end + timedelta(days=1), tz="UTC")
    date_values = pd.to_datetime(df["StartDateTimeGMT"], errors="coerce", utc=True)
    filtered_df = df[date_values.ge(start_timestamp) & date_values.lt(end_timestamp)].copy()

    st.caption(
        f"Selected period: {selected_start.strftime('%d/%m/%Y')} to {selected_end.strftime('%d/%m/%Y')} "
        f"({len(filtered_df):,} of {len(df):,} reports)"
    )
    return filtered_df, selected_start, selected_end


# =============================================================================
# Session-state data loading helpers
# =============================================================================


def request_signature(
    username: str,
    auth_method: str,
    start_date: date,
) -> dict[str, Any]:
    return {
        "endpoint": ODATA_ENDPOINT,
        "username_hash": sha256(username.encode("utf-8")).hexdigest()[:12],
        "auth_method": auth_method.lower(),
        "start_date": start_date.isoformat(),
    }


def transform_signature(raw_signature: dict[str, Any]) -> dict[str, Any]:
    return {
        **raw_signature,
        "value_signature": sha256("|".join(VALUE_ALIASES.keys()).encode("utf-8")).hexdigest()[:12],
    }


def view_signature(
    raw_signature: dict[str, Any],
    selected_vessels: list[str],
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    return {
        **raw_signature,
        "selected_vessels": tuple(selected_vessels),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "value_signature": sha256("|".join(VALUE_ALIASES.keys()).encode("utf-8")).hexdigest()[:12],
    }


def get_loaded_state() -> tuple[pd.DataFrame | None, pd.DataFrame | None, dict[str, Any] | None]:
    raw_df = st.session_state.get("loaded_raw_df")
    transformed_df = st.session_state.get("loaded_transformed_df")
    metadata = st.session_state.get("loaded_metadata")
    return raw_df, transformed_df, metadata


def set_loaded_raw_state(
    raw_df: pd.DataFrame,
    metadata: dict[str, Any],
    signature: dict[str, Any],
) -> None:
    metadata = metadata.copy()
    loaded_at_utc = datetime.now(timezone.utc)
    metadata["loaded_at_utc"] = metadata.get("loaded_at_utc") or loaded_at_utc.strftime("%d-%m-%Y %H:%M:%S UTC")
    metadata["loaded_at_local"] = metadata.get("loaded_at_local") or local_time_label(loaded_at_utc)
    metadata["loaded_start_date"] = signature["start_date"]
    st.session_state["loaded_raw_df"] = raw_df
    st.session_state["loaded_metadata"] = metadata
    st.session_state["loaded_request_signature"] = signature
    # The raw data changed, so any transformed data from the previous raw pull is stale.
    st.session_state.pop("loaded_transformed_df", None)
    st.session_state.pop("loaded_transform_signature", None)


def set_loaded_transform_state(df: pd.DataFrame, signature: dict[str, Any]) -> None:
    st.session_state["loaded_transformed_df"] = df
    st.session_state["loaded_transform_signature"] = signature



def raw_data_covers_request(
    loaded_signature: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    requested_signature: dict[str, Any],
    requested_start_date: date,
) -> bool:
    if not loaded_signature or not metadata:
        return False

    # If the same API/user/auth data was fetched from an earlier start date, it
    # also covers later start-date selections. No new API call is needed.
    for key in ["endpoint", "username_hash", "auth_method"]:
        if loaded_signature.get(key) != requested_signature.get(key):
            return False

    loaded_start_text = metadata.get("loaded_start_date") or loaded_signature.get("start_date")
    try:
        loaded_start_date = date.fromisoformat(str(loaded_start_text))
    except ValueError:
        return False

    return loaded_start_date <= requested_start_date

# =============================================================================
# Main app
# =============================================================================


def main() -> None:
    run_warmup_if_requested()
    require_dashboard_password()
    apply_custom_css()

    username = read_secret("MARORKA_USERNAME")
    password = read_secret("MARORKA_PASSWORD")
    token = read_secret("MARORKA_TOKEN")
    auth_method = read_secret("MARORKA_AUTH_METHOD", "basic")

    if auth_method.lower() in {"basic", "digest"} and (not username or not password):
        st.info("Add MARORKA_USERNAME and MARORKA_PASSWORD to .streamlit/secrets.toml.")
        st.stop()

    start_date, end_date, selected_group, selected_vessels, refresh = sidebar_controls()
    render_header(selected_group, selected_vessels)

    raw_signature = request_signature(username, auth_method, start_date)
    current_raw_signature = st.session_state.get("loaded_request_signature")
    raw_df, df, metadata = get_loaded_state()

    needs_raw_load = (
        refresh
        or raw_df is None
        or metadata is None
        or not raw_data_covers_request(current_raw_signature, metadata, raw_signature, start_date)
    )

    if needs_raw_load:
        if refresh:
            fetch_report_data.clear()
            cached_transform_report_data.clear()
            st.session_state.pop("loaded_raw_df", None)
            st.session_state.pop("loaded_metadata", None)
            st.session_state.pop("loaded_request_signature", None)
            st.session_state.pop("loaded_transformed_df", None)
            st.session_state.pop("loaded_transform_signature", None)

        try:
            with st.spinner("Loading API..."):
                raw_df, metadata = fetch_report_data(
                    username=username,
                    password=password,
                    token=token,
                    auth_method=auth_method,
                    start_date=start_date,
                )
            set_loaded_raw_state(raw_df, metadata, raw_signature)
            df = None
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            st.error(f"Marorka API request failed with status {status}.")
            st.caption("If credentials are correct, try MARORKA_AUTH_METHOD = 'digest'.")
            if exc.response is not None and exc.response.request is not None:
                st.code(exc.response.request.url, language="text")
            st.stop()
        except (MarorkaConfigError, ValueError, requests.RequestException) as exc:
            st.error(str(exc))
            st.stop()

    transform_sig = transform_signature(raw_signature)
    current_transform_sig = st.session_state.get("loaded_transform_signature")
    all_df = st.session_state.get("loaded_transformed_df")
    metadata = st.session_state.get("loaded_metadata")
    raw_df = st.session_state.get("loaded_raw_df")

    if raw_df is None or metadata is None:
        st.error("API data was not loaded. Please press Refresh API data or try again.")
        st.stop()

    if all_df is None or current_transform_sig != transform_sig:
        try:
            transform_started_at = time.perf_counter()
            all_df = cached_transform_report_data(raw_df)
            set_loaded_transform_state(all_df, transform_sig)
            metadata = st.session_state.get("loaded_metadata")
            if isinstance(metadata, dict):
                metadata["transform_seconds"] = round(time.perf_counter() - transform_started_at, 2)
                st.session_state["loaded_metadata"] = metadata
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
            st.stop()

    view_sig = view_signature(raw_signature, selected_vessels, start_date, end_date)
    df = filter_reports_for_selection(all_df, selected_vessels, start_date, end_date)

    if df.empty:
        st.warning("No matching water/waste report values were returned for the selected fleet/date window.")
        st.stop()

    render_api_load_caption(metadata)

    tab_dashboard, tab_diagnostics, tab_data = st.tabs(["Dashboard", "API Diagnostics", "Dataset"])

    if metadata.get("hit_page_limit"):
        st.warning(
            "The API refresh reached the page safety limit. The loaded dataset may be incomplete. "
            "Check API Diagnostics before using the report."
        )

    with tab_dashboard:
        dashboard_df, dashboard_start_date, dashboard_end_date = render_dashboard_date_slicer(df)
        if dashboard_df.empty:
            st.warning("No reports match the selected water/waste period.")
            st.stop()

    with st.sidebar.expander("Report Filters", expanded=False):
        st.caption("These filters affect the summary cards, latest report table, filtered table, and Excel export.")
        report_filter_specs = render_excel_like_filters(
            dashboard_df,
            key_prefix="water_waste_report_filter",
            label="Columns to filter",
            default_columns=DEFAULT_REPORT_FILTER_COLUMNS,
            default_numeric_filters=DEFAULT_REPORT_NUMERIC_FILTERS,
            default_categorical_filters=DEFAULT_REPORT_CATEGORICAL_FILTERS,
        )

    report_view_df = apply_excel_like_filters(dashboard_df, report_filter_specs)

    with tab_dashboard:
        st.markdown('<div class="section-title">metrics Summary</div>', unsafe_allow_html=True)
        render_kpis(report_view_df)
        if len(report_view_df) != len(dashboard_df):
            st.caption(
                f"Report filters use {len(report_view_df):,} of {len(dashboard_df):,} reports."
            )

        st.markdown('<div class="section-title">Latest Report By Vessel</div>', unsafe_allow_html=True)
        st.dataframe(make_display_dataframe(latest_by_vessel(report_view_df)), use_container_width=True, hide_index=True)

        st.markdown('<div class="section-title">Filtered Report Table</div>', unsafe_allow_html=True)
        sorted_dashboard_df = report_view_df.sort_values("EndDateTimeGMT", ascending=False)
        preview_dashboard_df = sorted_dashboard_df.head(TABLE_PREVIEW_ROW_LIMIT)
        display_df = make_display_dataframe(preview_dashboard_df)
        st.dataframe(display_df, use_container_width=True, hide_index=True)
        if len(sorted_dashboard_df) > TABLE_PREVIEW_ROW_LIMIT:
            st.caption(
                f"Showing first {TABLE_PREVIEW_ROW_LIMIT:,} of {len(sorted_dashboard_df):,} rows. "
                "Use the Dataset tab and Excel export for the full selected dataset."
            )

    with tab_diagnostics:
        st.markdown('<div class="section-title">Diagnostics</div>', unsafe_allow_html=True)
        diagnostics = pd.DataFrame(
            {
                "Metric": [
                    "Selected vessels",
                    "API start date",
                    "API end date",
                    "Dashboard selected start",
                    "Dashboard selected end",
                    "API loaded at",
                    "API loaded from start date",
                    "Selected-vessel water/waste reports",
                    "All-vessel transformed water/waste reports",
                    "Kept compact raw rows",
                    "Original API rows scanned",
                    "Discarded irrelevant rows",
                    "API pages",
                    "Downloaded MB",
                    "API fetch seconds",
                    "Transform seconds",
                    "Hit API page limit",
                ],
                "Value": [
                    ", ".join(selected_vessels),
                    start_date.isoformat(),
                    end_date.isoformat(),
                    dashboard_start_date.isoformat(),
                    dashboard_end_date.isoformat(),
                    metadata.get("loaded_at_local") or metadata.get("loaded_at_utc", "-"),
                    metadata.get("loaded_start_date", "-"),
                    f"{len(report_view_df):,}",
                    f"{len(all_df):,}",
                    f"{metadata.get('kept_rows', metadata.get('rows', 0)):,}",
                    f"{metadata.get('scanned_rows', 0):,}",
                    f"{metadata.get('discarded_rows', 0):,}",
                    f"{metadata['pages']:,}",
                    metadata["downloaded_mb"],
                    metadata.get("fetch_seconds", "-"),
                    metadata.get("transform_seconds", "-"),
                    str(metadata.get("hit_page_limit", "-")),
                ],
            }
        )
        st.dataframe(diagnostics, use_container_width=True, hide_index=True)

        with st.expander("First API URL", expanded=False):
            st.code(metadata.get("first_url", "-"), language="text")

        st.markdown('<div class="section-title">Compact Raw Water/Waste ValueDescription Counts</div>', unsafe_allow_html=True)
        if st.button("Calculate raw value counts"):
            value_counts = raw_df.get("ValueDescription", pd.Series(dtype="object")).value_counts(dropna=False).reset_index()
            value_counts.columns = ["ValueDescription", "Compact raw rows"]
            st.dataframe(value_counts.head(200), use_container_width=True, hide_index=True)
        else:
            st.caption("Raw value counts are calculated on demand so diagnostics do not slow normal loads.")

    with tab_data:
        export_df = report_view_df.sort_values(["ShipName", "EndDateTimeGMT"], ascending=[True, False])
        export_ready = (
            st.session_state.get("fleet_export_signature") == view_sig
            and "fleet_export_bytes" in st.session_state
        )

        if st.button("Prepare Excel download", type="primary"):
            with st.spinner("Preparing Excel file..."):
                st.session_state["fleet_export_bytes"] = to_excel_bytes(export_df)
                st.session_state["fleet_export_signature"] = view_sig
            export_ready = True

        if export_ready:
            st.download_button(
                "Download metrics Excel",
                data=st.session_state["fleet_export_bytes"],
                file_name="water_waste_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            st.caption("Excel generation is prepared on demand so normal dashboard loads stay faster.")

        preview_export_df = export_df.head(TABLE_PREVIEW_ROW_LIMIT)
        st.dataframe(make_display_dataframe(preview_export_df), use_container_width=True, hide_index=True)
        if len(export_df) > TABLE_PREVIEW_ROW_LIMIT:
            st.caption(
                f"Showing first {TABLE_PREVIEW_ROW_LIMIT:,} of {len(export_df):,} selected rows. "
                "The Excel download includes the full selected dataset."
            )


if __name__ == "__main__":
    main()
