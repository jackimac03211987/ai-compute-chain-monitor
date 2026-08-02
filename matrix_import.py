# -*- coding: utf-8 -*-
"""Extract listed-company candidates from user-provided industry matrices.

Usage:
  1. Put .xlsx/.xls/.csv matrix files into ./matrices/
  2. Run: python matrix_import.py
  3. Review ./data/watchlist_candidates.csv
  4. If the recognized rows are correct, run:
       python matrix_import.py --promote-recognized
     Or after manually filling needs_review rows, run:
       python matrix_import.py --from-candidates data/watchlist_candidates.csv

The script deliberately does not auto-promote ambiguous text. The production
fetcher only reads ./data/watchlist.json after review.
"""

import argparse
import ast
import csv
import datetime as dt
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from watchlist_loader import apply_company_overrides
from watchlist_audit import audit_rows, write_audit


BASE = Path(__file__).parent
MATRIX_DIR = BASE / "matrices"
DATA_DIR = BASE / "data"
DECISIONS_PATH = DATA_DIR / "watchlist_review_decisions.json"
SOURCE_EXTS = {".csv", ".xlsx", ".xls"}
XML_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

MANUAL_NAME_ALIASES = {
    "alphabetgoogle": "GOOGL",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "taiwansemiconductor": "TSM",
    "tsmc": "TSM",
    "supermicro": "SMCI",
    "supermicrocomputer": "SMCI",
    "honhaifoxconn": "2317.TW",
    "foxconn": "2317.TW",
    "mediatek": "2454.TW",
    "联发科": "2454.TW",
    "ase": "3711.TW",
    "日月光": "3711.TW",
    "cambricon": "688256.SS",
    "smic": "0981.HK",
    "中芯国际": "0981.HK",
    "airtel": "BHARTIARTL.NS",
    "bhartiairtel": "BHARTIARTL.NS",
    "honeywellquantinuum": "HON",
    "nttdatanntglobaldc": "9432.T",
    "nttdatanttglobledc": "9432.T",
    "nttdatanttglobaldc": "9432.T",
    "nttdata": "9432.T",
    "nationalgrid": "NG.L",
    "rollsroyce": "RR.L",
    "tokyoelectron": "8035.T",
    "delta": "2308.TW",
    "midea": "000333.SZ",
    "美的集团": "000333.SZ",
    "zijinmining": "601899.SS",
    "紫金矿业": "601899.SS",
    "luxshareprecision": "002475.SZ",
    "立讯精密": "002475.SZ",
    "chinatelcom": "0728.HK",
    "中国电信": "0728.HK",
    "jdcom": "9618.HK",
    "京东": "9618.HK",
    "coreweave": "CRWV",
    "tfc": "300394.SZ",
    "天孚通信": "300394.SZ",
    "sonygroup": "6758.T",
    "sony": "6758.T",
    "johnsoncontrols": "JCI",
    "cbre": "CBRE",
    "unimicron": "3037.TW",
    "欣兴电子": "3037.TW",
    "胜宏科技": "300476.SZ",
    "hyundai": "005380.KS",
    "legrand": "LR.PA",
    "celestica": "CLS",
    "exelon": "EXC",
    "corning": "GLW",
    "jabil": "JBL",
    "singtel": "Z74.SI",
    "orange": "ORA.PA",
    "ericsson": "ERIC",
    "ibiden": "4062.T",
    "揖斐电": "4062.T",
    "telefonica": "TEF.MC",
    "telefnica": "TEF.MC",
    "ciena": "CIEN",
    "快手": "1024.HK",
    "telstra": "TLS.AX",
    "ppl": "PPL",
    "murata": "6981.T",
    "nokia": "NOK",
    "洛阳钼业": "603993.SS",
    "中广核": "1816.HK",
    "中国铁塔": "0788.HK",
    "中国联通": "0762.HK",
    "mongodb": "MDB",
    "北方稀土": "600111.SS",
    "中国核电": "601985.SS",
    "keysight": "KEYS",
    "nutanix": "NTNX",
    "中国铝业": "2600.HK",
    "telus": "TU",
    "中科曙光": "603019.SS",
    "vodafone": "VOD",
    "隆基绿能": "601012.SS",
    "bt": "BT-A.L",
    "ionq": "IONQ",
    "特变电工": "600089.SS",
    "中航光电": "002179.SZ",
    "tongfu": "002156.SZ",
    "紫光股份": "000938.SZ",
    "jll": "JLL",
    "borgwarner": "BWA",
    "亿纬锂能": "300014.SZ",
    "nvent": "NVT",
    "keppel": "BN4.SI",
    "华大九天": "301269.SZ",
    "龙源电力": "0916.HK",
    "润泽科技": "300442.SZ",
    "地平线": "9660.HK",
    "华虹": "1347.HK",
    "capitaland": "9CI.SI",
    "用友网络": "600588.SS",
    "优必选": "9880.HK",
    "sembcorp": "U96.SI",
    "sensetime": "0020.HK",
    "商汤": "0020.HK",
    "oklo": "OKLO",
    "kingdee": "0268.HK",
    "金蝶": "0268.HK",
    "盛美上海": "688082.SS",
    "大华股份": "002236.SZ",
    "中国西电": "601179.SS",
    "中国稀土": "000831.SZ",
    "lumentechnologies": "LUMN",
    "kyndryl": "KD",
    "aes": "AES",
    "aaon": "AAON",
    "深信服": "300454.SZ",
    "中国通信服务": "0552.HK",
    "nextdc": "NXT.AX",
    "modine": "MOD",
    "锐捷网络": "301165.SZ",
    "paloaltonetworks": "PANW",
    "eqt": "EQT",
    "三峡能源": "600905.SS",
    "芯原股份": "688521.SS",
    "verisilicon": "688521.SS",
    "烽火通信": "600498.SS",
    "fiberhome": "600498.SS",
    "沃尔核材": "002130.SZ",
    "拓维信息": "002261.SZ",
    "soil": "010950.KS",
    "中国长城": "000066.SZ",
    "云南锗业": "002428.SZ",
    "麦格米特": "002851.SZ",
    "高新发展": "000628.SZ",
    "软通动力": "301236.SZ",
    "isoftstone": "301236.SZ",
    "许继电气": "000400.SZ",
    "美图": "1357.HK",
    "神州数码": "000034.SZ",
    "昆仑万维": "300418.SZ",
    "中科创达": "300496.SZ",
    "thundersoft": "300496.SZ",
    "rigetti": "RGTI",
    "mara": "MARA",
    "iren": "IREN",
    "belden": "BDC",
    "申菱环境": "301018.SZ",
    "金盘科技": "688676.SS",
    "科华数据": "002335.SZ",
    "kehuadata": "002335.SZ",
    "江波龙": "301308.SZ",
    "longsys": "301308.SZ",
    "同方股份": "600100.SS",
    "华天科技": "002185.SZ",
    "全志科技": "300458.SZ",
    "jinko": "JKS",
    "cogent": "CCOI",
    "同飞股份": "300990.SZ",
    "黑芝麻智能": "2533.HK",
    "鸿博股份": "002229.SZ",
    "科士达": "002518.SZ",
    "盛科通信": "688702.SS",
    "概伦电子": "688206.SS",
    "明阳电气": "301291.SZ",
    "弘信电子": "300657.SZ",
    "奇安信": "688561.SS",
    "太辰光": "300570.SZ",
    "天岳先进": "688234.SS",
    "国电南京自动化": "600268.SS",
    "南都电源": "300068.SZ",
    "协鑫能科": "002015.SZ",
    "云赛智联": "600602.SS",
    "中科飞测": "688361.SS",
    "东华软件": "002065.SZ",
    "ucloud": "688158.SS",
    "优刻得": "688158.SS",
    "soundhoundai": "SOUN",
    "fluence": "FLNC",
    "cushmanwakefield": "CWK",
    "高澜股份": "300499.SZ",
    "飞荣达": "300602.SZ",
    "良信股份": "002706.SZ",
    "网宿科技": "300017.SZ",
    "第四范式": "6682.HK",
    "源杰科技": "688498.SS",
    "润建股份": "002929.SZ",
    "浪潮软件": "600756.SS",
    "泰豪科技": "600590.SS",
    "数据港": "603881.SS",
    "恒为科技": "603496.SS",
    "光环新网": "300383.SZ",
    "仕佳光子": "688313.SS",
    "云天励飞": "688343.SS",
    "中贝通信": "603220.SS",
    "中恒电气": "002364.SZ",
    "露笑科技": "002617.SZ",
    "长光华芯": "688048.SS",
    "欧陆通": "300870.SZ",
    "星环科技": "688031.SS",
    "易事特": "300376.SZ",
    "拓尔思": "300229.SZ",
    "奥飞数据": "300738.SZ",
    "万兴科技": "300624.SZ",
    "鹏博士": "600804.SS",
    "首都在线": "300846.SZ",
    "海量数据": "603138.SS",
    "海天瑞声": "688787.SS",
    "并行科技": "839493.BJ",
    "利通电子": "603629.SS",
    "亿华通": "688339.SS",
    "云从科技": "688327.SS",
    "依米康": "300249.SZ",
    "佳力图": "603912.SS",
    "曙光数创": "872808.BJ",
    "青云科技": "688316.SS",
    "lgelectronics": "066570.KS",
    "dwave": "QBTS",
    "plugpower": "PLUG",
    "tatacommunications": "TATACOMM.NS",
    "munters": "MTRS.ST",
    "topco": "5434.TWO",
    "科泰电源": "300153.SZ",
    "hivedigital": "HIVE",
    "appen": "APX.AX",
    "magnora": "MGN.OL",
    "pilotenergy": "PGY.AX",
    "duosedgeai": "DUOT",
    "tekcapital": "TEK.L",
    "dxn": "DXN.AX",
    "reaboldresources": "RBD.L",
    "2genergy": "2GB.DE",
    "adanigroup": "ADANIENT.NS",
    "adani": "ADANIENT.NS",
    "mtn": "MTN.JO",
    "dmgblockchain": "DMGI.V",
    "celsia": "CELSIA.CL",
    "智谱": "2513.HK",
    "智谱上市": "2513.HK",
    "knowledgeatlas": "2513.HK",
    "biren": "6082.HK",
    "biren上市": "6082.HK",
    "壁仞科技": "6082.HK",
    "moorethreads": "688795.SS",
    "moorethreads上市": "688795.SS",
    "摩尔线程": "688795.SS",
    "metax": "688802.SS",
    "沐曦": "688802.SS",
    "iluvatarcorex": "9903.HK",
    "天数智芯": "9903.HK",
}


def load_builtin_companies():
    src = (BASE / "fetch_data.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "C":
                    return apply_company_overrides(BASE, ast.literal_eval(node.value), allow_new=True)
    raise RuntimeError("Cannot find C watchlist dictionary in fetch_data.py")


def normalize_ticker(raw):
    s = raw.strip().upper().replace("：", ":")
    s = s.replace(" ", "")
    s = re.sub(r"^(NASDAQ|NYSE|AMEX|HKEX|SSE|SZSE|TSE|TWSE|TPEX|KRX|KOSDAQ|XETRA|EURONEXT)[:：]", "", s)
    s = s.strip("$()[]{}")

    if re.fullmatch(r"\d{6}", s):
        if s.startswith(("60", "68")):
            return s + ".SS"
        if s.startswith(("00", "30")):
            return s + ".SZ"
    if re.fullmatch(r"\d{4}", s):
        return s + ".HK"
    if re.fullmatch(r"\d{4,6}\.(TW|TWO|KS|KQ|T|HK|SS|SZ|BJ|KL|SI|SR)", s):
        return s
    if re.fullmatch(r"[A-Z0-9-]{1,12}(\.[A-Z]{1,4})?", s):
        return s
    return None


def normalize_name(value):
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "").lower())


def name_tokens(value):
    text = str(value or "").lower()
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text)
    keep = []
    for token in tokens:
        if re.fullmatch(r"[a-z0-9]+", token) and len(token) >= 3:
            keep.append(token)
        elif re.search(r"[\u4e00-\u9fff]", token) and len(token) >= 2:
            keep.append(token)
    return keep


def build_name_index(known):
    full = {}
    token_hits = {}
    for ticker, values in known.items():
        name = values[0]
        aliases = {normalize_name(name)}
        aliases.update(normalize_name(token) for token in name_tokens(name))
        for alias in aliases:
            if alias:
                full.setdefault(alias, set()).add(ticker)
        for token in name_tokens(name):
            token_hits.setdefault(normalize_name(token), set()).add(ticker)

    for alias, ticker in MANUAL_NAME_ALIASES.items():
        if ticker in known:
            full.setdefault(alias, set()).add(ticker)

    return full, token_hits


def match_company_name(name, full_index, token_index):
    normalized = normalize_name(name)
    candidates = full_index.get(normalized, set())
    if len(candidates) == 1:
        return next(iter(candidates))

    for token in name_tokens(name):
        token_key = normalize_name(token)
        candidates = full_index.get(token_key, set()) or token_index.get(token_key, set())
        if len(candidates) == 1:
            return next(iter(candidates))
    return None


PATTERNS = [
    re.compile(r"\$[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b"),
    re.compile(r"\b(?:NASDAQ|NYSE|AMEX|HKEX|SSE|SZSE|TSE|TWSE|TPEX|KRX|KOSDAQ|XETRA|EURONEXT)[:：]\s*[A-Za-z0-9.]{1,10}\b", re.I),
    re.compile(r"\b\d{6}\b"),
    re.compile(r"\b\d{4,6}\.(?:TW|TWO|KS|KQ|T|HK|SS|SZ|BJ|KL|SI)\b", re.I),
    re.compile(r"\b[A-Z]{1,5}\s+US\b"),
]


def extract_tickers(text):
    found = []
    for pat in PATTERNS:
        for match in pat.findall(text):
            candidate = re.sub(r"\s+US$", "", str(match).upper()).strip()
            normalized = normalize_ticker(candidate)
            if normalized:
                found.append(normalized)
    return found


def iter_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for r, row in enumerate(reader, start=1):
            for c, value in enumerate(row, start=1):
                yield r, c, str(value)


def col_index(cell_ref):
    letters = re.match(r"[A-Z]+", str(cell_ref or "").upper())
    if not letters:
        return None
    value = 0
    for char in letters.group(0):
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value


def xlsx_text(node):
    return "".join(t.text or "" for t in node.findall(".//main:t", XML_NS))


def xlsx_shared_strings(zf):
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [xlsx_text(si) for si in root.findall("main:si", XML_NS)]


def xlsx_sheet_paths(zf):
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rels = {}
    for rel in rels_root.findall("pkgrel:Relationship", XML_NS):
        target = rel.attrib.get("Target", "")
        target = target.lstrip("/")
        rels[rel.attrib.get("Id")] = target if target.startswith("xl/") else "xl/" + target

    sheets = []
    for sheet in workbook.findall("main:sheets/main:sheet", XML_NS):
        rel_id = sheet.attrib.get(f"{{{XML_NS['rel']}}}id")
        path = rels.get(rel_id)
        if path:
            sheets.append((sheet.attrib.get("name", ""), path))
    return sheets


def xlsx_cell_value(cell, shared_strings):
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return xlsx_text(cell)

    value_node = cell.find("main:v", XML_NS)
    if value_node is None:
        return ""
    raw = value_node.text or ""
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except Exception:
            return raw
    return raw


def iter_xlsx_rows(path):
    with zipfile.ZipFile(path) as zf:
        shared_strings = xlsx_shared_strings(zf)
        for sheet_name, sheet_path in xlsx_sheet_paths(zf):
            root = ET.fromstring(zf.read(sheet_path))
            for row in root.findall(".//main:sheetData/main:row", XML_NS):
                try:
                    row_num = int(row.attrib.get("r", "0"))
                except ValueError:
                    row_num = 0
                values = {}
                for cell in row.findall("main:c", XML_NS):
                    col = col_index(cell.attrib.get("r"))
                    if col is None:
                        continue
                    value = xlsx_cell_value(cell, shared_strings)
                    if value != "":
                        values[col] = str(value)
                yield sheet_name, row_num, values


def iter_excel(path):
    if path.suffix.lower() == ".xlsx":
        for sheet, row_num, values in iter_xlsx_rows(path):
            for col, value in values.items():
                yield f"{sheet}:{row_num}", col, str(value)
        return

    try:
        import pandas as pd
    except Exception as exc:
        raise SystemExit("Legacy .xls import requires pandas in this environment") from exc

    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str)
    for sheet, df in sheets.items():
        for r, row in df.iterrows():
            for c, value in row.items():
                if value == value:
                    yield f"{sheet}:{r+1}", c + 1, str(value)


def iter_matrix_cells(path):
    if path.suffix.lower() == ".csv":
        yield from iter_csv(path)
    else:
        yield from iter_excel(path)


def iter_listed_entities(path):
    if path.suffix.lower() == ".csv":
        return
    if path.suffix.lower() == ".xlsx":
        headers_by_sheet = {}
        for sheet, row_num, values in iter_xlsx_rows(path):
            if not str(sheet).startswith("企业主体清单"):
                continue
            if row_num == 1:
                headers_by_sheet[sheet] = {str(value).strip(): col for col, value in values.items()}
                continue
            columns = headers_by_sheet.get(sheet, {})
            name_col = columns.get("企业名称")
            type_col = columns.get("价值类型")
            value_col = columns.get("价值 (USD)") or columns.get("价值(USD)") or columns.get("价值")
            if name_col is None or type_col is None:
                continue
            name = str(values.get(name_col, "") or "").strip()
            value_type = str(values.get(type_col, "") or "").strip()
            if not name or "上市" not in value_type:
                continue
            value = str(values.get(value_col, "") or "").strip() if value_col is not None else ""
            yield {
                "name": name,
                "value_type": value_type,
                "value": value,
                "source": path.name,
                "context": f"{path.name}@{sheet}:{row_num}: {name} | {value_type} | {value}",
            }
        return

    try:
        import pandas as pd
    except Exception as exc:
        raise SystemExit("Legacy .xls import requires pandas in this environment") from exc

    sheets = pd.read_excel(path, sheet_name=None, dtype=str)
    for sheet, df in sheets.items():
        if not str(sheet).startswith("企业主体清单"):
            continue
        columns = {str(col).strip(): col for col in df.columns}
        name_col = columns.get("企业名称")
        type_col = columns.get("价值类型")
        value_col = columns.get("价值 (USD)") or columns.get("价值(USD)") or columns.get("价值")
        if name_col is None or type_col is None:
            continue
        for idx, row in df.iterrows():
            name = str(row.get(name_col, "") or "").strip()
            value_type = str(row.get(type_col, "") or "").strip()
            if not name or "上市" not in value_type:
                continue
            value = str(row.get(value_col, "") or "").strip() if value_col is not None else ""
            yield {
                "name": name,
                "value_type": value_type,
                "value": value,
                "source": path.name,
                "context": f"{path.name}@{sheet}:{idx + 2}: {name} | {value_type} | {value}",
            }


def matrix_source_files(matrix_dir=MATRIX_DIR):
    matrix_dir = Path(matrix_dir)
    matrix_dir.mkdir(exist_ok=True)
    return [
        path for path in sorted(matrix_dir.iterdir())
        if path.is_file() and path.suffix.lower() in SOURCE_EXTS
    ]


def row_from_known(ticker, known, sources, contexts):
    name, country, city, lat, lon, seg = known[ticker]
    return {
        "t": ticker,
        "name": name,
        "country": country,
        "city": city,
        "lat": lat,
        "lon": lon,
        "seg": seg,
        "matrix_sources": sorted(sources),
        "matrix_context": " | ".join(contexts[:4])[:500],
    }


def row_from_unknown_name(name, sources, contexts):
    return {
        "t": "",
        "name": name,
        "country": "",
        "city": "",
        "lat": "",
        "lon": "",
        "seg": "",
        "matrix_sources": sorted(sources),
        "matrix_context": " | ".join(contexts[:4])[:500],
    }


def load_review_decisions(path=DECISIONS_PATH):
    path = Path(path)
    if not path.exists():
        return {}

    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("excluded") or payload.get("decisions") or []
    decisions = {}
    for row in rows:
        keys = set()
        for value in row.get("names", []):
            key = normalize_name(value)
            if key:
                keys.add(key)
        for value in row.get("tickers", []):
            try:
                keys.add(normalize_ticker(value))
            except Exception:
                key = str(value or "").strip().upper()
                if key:
                    keys.add(key)
        for key in keys:
            decisions[key] = row
    return decisions


def split_review_decisions(unrecognized, decisions):
    if not decisions:
        return unrecognized, []

    remaining = []
    excluded = []
    for row in unrecognized:
        keys = []
        if row.get("t"):
            try:
                keys.append(normalize_ticker(row["t"]))
            except Exception:
                keys.append(str(row["t"]).strip().upper())
        if row.get("name"):
            keys.append(normalize_name(row["name"]))
        decision = next((decisions.get(key) for key in keys if key in decisions), None)
        if decision:
            excluded.append({
                **row,
                "review_decision": str(decision.get("decision", "excluded")),
                "exclude_reason": str(decision.get("reason", "")),
            })
        else:
            remaining.append(row)
    return remaining, excluded


def scan_matrices(matrix_dir=MATRIX_DIR):
    known = load_builtin_companies()
    full_index, token_index = build_name_index(known)
    hits = {}
    unknown_tickers = {}
    unknown_names = {}
    matrix_dir = Path(matrix_dir)
    sources = matrix_source_files(matrix_dir)
    if not sources:
        supported = ", ".join(sorted(SOURCE_EXTS))
        raise SystemExit(f"No matrix files found in {matrix_dir}. Add {supported} files first.")
    for path in sources:
        for row_ref, col_ref, value in iter_matrix_cells(path):
            value = value.strip()
            if not value:
                continue
            for ticker in extract_tickers(value):
                bucket = hits if ticker in known else unknown_tickers
                item = bucket.setdefault(ticker, {"sources": set(), "contexts": []})
                item["sources"].add(path.name)
                item["contexts"].append(f"{path.name}@{row_ref},{col_ref}: {value}")

        for entity in iter_listed_entities(path):
            ticker = match_company_name(entity["name"], full_index, token_index)
            if ticker and ticker in known:
                item = hits.setdefault(ticker, {"sources": set(), "contexts": []})
            else:
                key = normalize_name(entity["name"])
                item = unknown_names.setdefault(key, {"name": entity["name"], "sources": set(), "contexts": []})
            item["sources"].add(entity["source"])
            item["contexts"].append(entity["context"])

    recognized = [row_from_known(t, known, v["sources"], v["contexts"]) for t, v in sorted(hits.items())]
    unrecognized = [
        {
            "t": t,
            "name": "",
            "country": "",
            "city": "",
            "lat": "",
            "lon": "",
            "seg": "",
            "matrix_sources": sorted(v["sources"]),
            "matrix_context": " | ".join(v["contexts"][:4])[:500],
        }
        for t, v in sorted(unknown_tickers.items())
    ]
    unrecognized += [
        row_from_unknown_name(v["name"], v["sources"], v["contexts"])
        for _, v in sorted(unknown_names.items())
    ]
    decisions = load_review_decisions()
    unrecognized, excluded = split_review_decisions(unrecognized, decisions)
    return recognized, unrecognized, excluded


def write_candidates(recognized, unrecognized, excluded=None):
    excluded = excluded or []
    DATA_DIR.mkdir(exist_ok=True)
    csv_path = DATA_DIR / "watchlist_candidates.csv"
    fields = ["status", "t", "name", "country", "city", "lat", "lon", "seg", "matrix_sources", "matrix_context", "review_decision", "exclude_reason"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in recognized:
            out = {**row, "status": "recognized", "matrix_sources": ";".join(row["matrix_sources"])}
            writer.writerow(out)
        for row in unrecognized:
            out = {**row, "status": "needs_review", "matrix_sources": ";".join(row["matrix_sources"])}
            writer.writerow(out)
        for row in excluded:
            out = {**row, "status": "excluded", "matrix_sources": ";".join(row["matrix_sources"])}
            writer.writerow(out)
    return csv_path


def write_summary(recognized, unrecognized, excluded=None):
    excluded = excluded or []
    path = DATA_DIR / "matrix_import_summary.json"
    all_rows = recognized + unrecognized + excluded
    source_counts = {}
    for row in all_rows:
        for source in row.get("matrix_sources", []):
            source_counts[source] = source_counts.get(source, 0) + 1

    payload = {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_candidates": len(all_rows),
        "recognized": len(recognized),
        "needs_review": len(unrecognized),
        "excluded": len(excluded),
        "sources": dict(sorted(source_counts.items())),
        "recognized_sample": [
            {
                "t": row["t"],
                "name": row["name"],
                "seg": row["seg"],
            }
            for row in recognized[:30]
        ],
        "needs_review_sample": [
            {
                "name_or_ticker": row.get("name") or row.get("t") or "",
                "context": row.get("matrix_context", ""),
            }
            for row in unrecognized[:80]
        ],
        "excluded_sample": [
            {
                "name_or_ticker": row.get("name") or row.get("t") or "",
                "reason": row.get("exclude_reason", ""),
                "context": row.get("matrix_context", ""),
            }
            for row in excluded[:80]
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def promote(recognized):
    if not recognized:
        raise ValueError("No recognized rows to promote. Review candidates or add company overrides first.")
    path = DATA_DIR / "watchlist.json"
    payload = {
        "source": "industry_matrix_import",
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "review_status": "recognized_rows_promoted",
        "companies": recognized,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_audit(DATA_DIR / "watchlist_audit.json", audit_rows(recognized))
    return path


def parse_float(value, field, ticker):
    try:
        return float(str(value).strip())
    except Exception as exc:
        raise ValueError(f"{ticker}: invalid {field}: {value!r}") from exc


def normalize_sources(value):
    if isinstance(value, list):
        return value
    return [x for x in str(value or "").split(";") if x]


def validate_review_row(row):
    ticker = normalize_ticker(row.get("t", ""))
    if not ticker:
        raise ValueError(f"invalid ticker: {row.get('t')!r}")

    required = ["name", "country", "city", "seg"]
    missing = [field for field in required if not str(row.get(field, "")).strip()]
    if missing:
        raise ValueError(f"{ticker}: missing required fields: {', '.join(missing)}")

    lat = parse_float(row.get("lat", ""), "lat", ticker)
    lon = parse_float(row.get("lon", ""), "lon", ticker)
    if not -90 <= lat <= 90:
        raise ValueError(f"{ticker}: lat out of range: {lat}")
    if not -180 <= lon <= 180:
        raise ValueError(f"{ticker}: lon out of range: {lon}")

    return {
        "t": ticker,
        "name": row["name"].strip(),
        "country": row["country"].strip(),
        "city": row["city"].strip(),
        "lat": lat,
        "lon": lon,
        "seg": row["seg"].strip(),
        "matrix_sources": normalize_sources(row.get("matrix_sources", "")),
        "matrix_context": row.get("matrix_context", ""),
    }


def promote_from_candidates(path):
    path = Path(path)
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = str(row.get("status", "")).strip()
            if status not in {"recognized", "reviewed", "approved"}:
                continue
            rows.append(validate_review_row(row))

    if not rows:
        raise ValueError(f"No recognized/reviewed/approved rows in {path}")

    seen = set()
    deduped = []
    for row in rows:
        if row["t"] in seen:
            continue
        seen.add(row["t"])
        deduped.append(row)

    out = DATA_DIR / "watchlist.json"
    payload = {
        "source": "reviewed_matrix_candidates",
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "review_status": "reviewed_csv_promoted",
        "companies": deduped,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_audit(DATA_DIR / "watchlist_audit.json", audit_rows(deduped))
    return out, len(deduped)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix-dir", default=str(MATRIX_DIR), help="Directory containing .xlsx/.xls/.csv matrix files")
    ap.add_argument("--promote-recognized", action="store_true")
    ap.add_argument("--from-candidates", help="Promote reviewed candidate CSV into data/watchlist.json")
    args = ap.parse_args()
    if args.from_candidates:
        path, count = promote_from_candidates(args.from_candidates)
        print(f"watchlist: {path}")
        print(f"companies: {count}")
        return

    recognized, unrecognized, excluded = scan_matrices(args.matrix_dir)
    csv_path = write_candidates(recognized, unrecognized, excluded)
    summary_path = write_summary(recognized, unrecognized, excluded)
    audit_path = write_audit(DATA_DIR / "watchlist_candidates_audit.json", audit_rows([
        {**row, "status": "recognized"} for row in recognized
    ] + [
        {**row, "status": "needs_review"} for row in unrecognized
    ] + [
        {**row, "status": "excluded"} for row in excluded
    ]))
    print(f"recognized: {len(recognized)}")
    print(f"needs_review: {len(unrecognized)}")
    print(f"excluded: {len(excluded)}")
    print(f"candidates: {csv_path}")
    print(f"summary: {summary_path}")
    print(f"audit: {audit_path}")
    if args.promote_recognized:
        if not recognized:
            raise SystemExit("No recognized rows to promote. Review candidates or add company overrides first.")
        path = promote(recognized)
        print(f"watchlist: {path}")


if __name__ == "__main__":
    main()
