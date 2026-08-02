# -*- coding: utf-8 -*-
"""全球 AI 算力产业链上市公司行情采集 -> data/semi_market.json

行情通道：Yahoo v8 chart API（与 fetch_live.py 同一网络路径）。
yfinance 批量下载自 2026-06-27 起被 Yahoo 持续限流（YFRateLimitError），
本脚本不再依赖 yfinance；市值在拉不到新值时从上一份 semi_market.json 结转。
"""
import json, math, os, pathlib, sys, time, warnings
warnings.filterwarnings('ignore')
from aicm_io import data_lock, read_json, try_process_lock, write_json_atomic
from industry_chain import enrich_company
from watchlist_loader import active_universe_as_tuples, rebuild_active_universe

BASE = pathlib.Path(__file__).parent
OUT_PATH = BASE / "data" / "semi_market.json"

# ticker: (名称, 国家/地区, 城市, lat, lon, 细分)
C = {
# ===== 美国 =====
"NVDA":("NVIDIA 英伟达","美国","Santa Clara",37.35,-121.95,"GPU/AI芯片"),
"AMD":("AMD 超威","美国","Santa Clara",37.35,-121.96,"CPU/GPU"),
"INTC":("Intel 英特尔","美国","Santa Clara",37.39,-121.96,"IDM"),
"QCOM":("Qualcomm 高通","美国","San Diego",32.72,-117.16,"手机SoC"),
"AVGO":("Broadcom 博通","美国","Palo Alto",37.44,-122.14,"网络/定制AI"),
"TXN":("Texas Instruments 德州仪器","美国","Dallas",32.78,-96.80,"模拟"),
"MU":("Micron 美光","美国","Boise",43.62,-116.21,"存储"),
"ADI":("Analog Devices 亚德诺","美国","Wilmington MA",42.56,-71.17,"模拟"),
"MRVL":("Marvell 迈威尔","美国","Santa Clara",37.36,-121.94,"数据中心芯片"),
"ON":("onsemi 安森美","美国","Phoenix",33.45,-112.07,"功率"),
"MCHP":("Microchip 微芯","美国","Chandler",33.31,-111.84,"MCU"),
"LRCX":("Lam Research 泛林","美国","Fremont",37.55,-121.99,"设备"),
"AMAT":("Applied Materials 应用材料","美国","Santa Clara",37.38,-121.97,"设备"),
"KLAC":("KLA 科磊","美国","Milpitas",37.43,-121.90,"检测设备"),
"SNPS":("Synopsys 新思","美国","Sunnyvale",37.37,-122.04,"EDA"),
"CDNS":("Cadence 楷登","美国","San Jose",37.33,-121.89,"EDA"),
"TER":("Teradyne 泰瑞达","美国","North Reading",42.58,-71.08,"测试设备"),
"GFS":("GlobalFoundries 格芯","美国","Malta NY",42.98,-73.79,"代工"),
"SWKS":("Skyworks 思佳讯","美国","Irvine",33.68,-117.85,"射频"),
"QRVO":("Qorvo 威讯","美国","Greensboro",36.07,-79.79,"射频"),
"MPWR":("Monolithic Power 芯源","美国","Kirkland",47.68,-122.21,"电源管理"),
"ALAB":("Astera Labs","美国","Santa Clara",37.37,-121.98,"AI互连"),
"CRDO":("Credo","美国","San Jose",37.30,-121.87,"高速互连"),
"COHR":("Coherent 高意","美国","Saxonburg",40.75,-79.81,"光电"),
"ENTG":("Entegris 英特格","美国","Billerica",42.55,-71.27,"材料"),
"AMKR":("Amkor 安靠","美国","Tempe",33.43,-111.94,"封测"),
"RMBS":("Rambus","美国","San Jose",37.33,-121.92,"内存接口"),
"LSCC":("Lattice 莱迪思","美国","Hillsboro",45.52,-122.99,"FPGA"),
"SLAB":("Silicon Labs 芯科","美国","Austin",30.27,-97.74,"IoT芯片"),
"WOLF":("Wolfspeed","美国","Durham",35.99,-78.90,"碳化硅"),
"ONTO":("Onto Innovation","美国","Wilmington MA",42.55,-71.17,"检测设备"),
"FORM":("FormFactor","美国","Livermore",37.68,-121.77,"探针卡"),
"ACLS":("Axcelis 亚舍立","美国","Beverly",42.56,-70.88,"离子注入"),
"VECO":("Veeco","美国","Plainview",40.78,-73.47,"设备"),
"SITM":("SiTime","美国","Santa Clara",37.38,-121.95,"时钟芯片"),
"POWI":("Power Integrations","美国","San Jose",37.39,-121.93,"电源芯片"),
"CRUS":("Cirrus Logic 凌云","美国","Austin",30.26,-97.73,"音频芯片"),
"SYNA":("Synaptics 新突思","美国","San Jose",37.38,-121.92,"触控/连接"),
"DIOD":("Diodes 达尔","美国","Plano",33.02,-96.70,"分立器件"),
"VSH":("Vishay 威世","美国","Malvern",40.04,-75.51,"分立器件"),
"MTSI":("MACOM","美国","Lowell",42.63,-71.32,"射频/光"),
"SMTC":("Semtech","美国","Camarillo",34.22,-119.04,"LoRa/信号链"),
# 美股上市·总部他国
"ARM":("Arm 安谋","英国","Cambridge UK",52.21,0.12,"IP架构"),
"STM":("STMicro 意法半导体","瑞士","Geneva",46.20,6.14,"IDM"),
"NXPI":("NXP 恩智浦","荷兰","Eindhoven",51.44,5.48,"车规MCU"),
"ASML":("ASML 阿斯麦","荷兰","Veldhoven",51.42,5.40,"光刻机"),
"TSEM":("Tower 高塔","以色列","Migdal Haemek",32.68,35.24,"特色代工"),
"NVMI":("Nova 诺威","以色列","Rehovot",31.89,34.81,"量测"),
"CAMT":("Camtek 康代","以色列","Migdal Haemek",32.67,35.24,"检测"),
"HIMX":("Himax 奇景光电","中国台湾","Tainan",22.99,120.21,"显示驱动"),
# ===== 中国台湾 =====
"2330.TW":("台积电 TSMC","中国台湾","Hsinchu",24.81,120.97,"代工"),
"2454.TW":("联发科 MediaTek","中国台湾","Hsinchu",24.78,121.00,"手机SoC"),
"2303.TW":("联电 UMC","中国台湾","Hsinchu",24.77,120.98,"代工"),
"3711.TW":("日月光投控 ASE","中国台湾","Kaohsiung",22.62,120.30,"封测"),
"2379.TW":("瑞昱 Realtek","中国台湾","Hsinchu",24.80,120.99,"网络芯片"),
"3034.TW":("联咏 Novatek","中国台湾","Hsinchu",24.79,121.01,"显示驱动"),
"3661.TW":("世芯 Alchip","中国台湾","Taipei",25.06,121.56,"ASIC设计"),
"3443.TW":("创意 GUC","中国台湾","Hsinchu",24.78,120.99,"ASIC设计"),
"2408.TW":("南亚科 Nanya","中国台湾","New Taipei",25.01,121.46,"DRAM"),
"6770.TW":("力积电 PSMC","中国台湾","Hsinchu",24.81,120.98,"代工"),
"5347.TWO":("世界先进 VIS","中国台湾","Hsinchu",24.80,120.96,"代工"),
"3105.TWO":("稳懋 WIN Semi","中国台湾","Taoyuan",24.99,121.30,"砷化镓代工"),
# ===== 韩国 =====
"005930.KS":("三星电子 Samsung","韩国","Suwon",37.26,127.03,"IDM/存储"),
"000660.KS":("SK海力士 SK hynix","韩国","Icheon",37.27,127.44,"存储/HBM"),
"042700.KS":("韩美半导体 Hanmi","韩国","Incheon",37.45,126.70,"HBM设备"),
"403870.KQ":("HPSP","韩国","Hwaseong",37.20,126.83,"退火设备"),
# ===== 日本 =====
"8035.T":("东京电子 TEL","日本","Tokyo",35.68,139.76,"设备"),
"6857.T":("爱德万 Advantest","日本","Tokyo",35.69,139.74,"测试设备"),
"6723.T":("瑞萨 Renesas","日本","Tokyo",35.65,139.74,"车规MCU"),
"6146.T":("迪思科 DISCO","日本","Tokyo",35.59,139.73,"切割研磨"),
"7735.T":("迪恩士 SCREEN","日本","Kyoto",35.01,135.77,"清洗设备"),
"6963.T":("罗姆 ROHM","日本","Kyoto",34.97,135.77,"功率"),
"6920.T":("Lasertec","日本","Yokohama",35.47,139.62,"EUV检测"),
"6526.T":("Socionext","日本","Yokohama",35.45,139.63,"ASIC设计"),
"7729.T":("东京精密 Accretech","日本","Hachioji",35.66,139.32,"测量/切割"),
"4063.T":("信越化学 Shin-Etsu","日本","Tokyo",35.68,139.77,"硅片/材料"),
"3436.T":("SUMCO 胜高","日本","Tokyo",35.65,139.76,"硅片"),
"285A.T":("铠侠 Kioxia","日本","Tokyo",35.55,139.78,"NAND"),
# ===== 欧洲（本地上市）=====
"ASM.AS":("ASM International","荷兰","Almere",52.35,5.22,"ALD设备"),
"BESI.AS":("BE Semiconductor","荷兰","Duiven",51.94,6.02,"先进封装设备"),
"IFX.DE":("英飞凌 Infineon","德国","Munich",48.14,11.58,"功率/车规"),
"AIXA.DE":("爱思强 AIXTRON","德国","Herzogenrath",50.87,6.09,"MOCVD"),
"ELG.DE":("Elmos","德国","Dortmund",51.51,7.47,"车规芯片"),
"SMHN.DE":("Süss MicroTec","德国","Garching",48.25,11.65,"光刻配套"),
"SOI.PA":("Soitec","法国","Bernin",45.27,5.78,"SOI衬底"),
"MELE.BR":("Melexis 迈来芯","比利时","Ieper",50.85,2.89,"车规传感"),
"NOD.OL":("Nordic 北欧半导体","挪威","Trondheim",63.43,10.40,"蓝牙芯片"),
"UBXN.SW":("u-blox","瑞士","Thalwil",47.29,8.56,"定位芯片"),
"VACN.SW":("VAT Group","瑞士","Haag",47.21,9.49,"真空阀件"),
# ===== 中国大陆（A股+港股）=====
"0981.HK":("中芯国际 SMIC","中国大陆","Shanghai",31.23,121.47,"代工"),
"1347.HK":("华虹半导体 Hua Hong","中国大陆","Shanghai",31.25,121.50,"代工"),
"9660.HK":("地平线机器人 Horizon","中国大陆","Beijing",39.99,116.30,"智驾芯片"),
"1385.HK":("上海复旦微电 FMSH","中国大陆","Shanghai",31.29,121.50,"FPGA/安全"),
"603501.SS":("韦尔股份 Will Semi","中国大陆","Shanghai",31.22,121.48,"CIS图像传感"),
"002371.SZ":("北方华创 NAURA","中国大陆","Beijing",39.93,116.39,"设备"),
"688012.SS":("中微公司 AMEC","中国大陆","Shanghai",31.20,121.60,"刻蚀设备"),
"688041.SS":("海光信息 Hygon","中国大陆","Tianjin",39.08,117.20,"CPU/DCU"),
"688256.SS":("寒武纪 Cambricon","中国大陆","Beijing",39.98,116.31,"AI芯片"),
"688008.SS":("澜起科技 Montage","中国大陆","Shanghai",31.21,121.44,"内存接口"),
"603986.SS":("兆易创新 GigaDevice","中国大陆","Beijing",39.96,116.32,"NOR/MCU"),
"002049.SZ":("紫光国微 Unigroup Guoxin","中国大陆","Shenzhen",22.54,114.06,"安全芯片"),
"600584.SS":("长电科技 JCET","中国大陆","Wuxi",31.49,120.31,"封测"),
"002156.SZ":("通富微电 TFME","中国大陆","Nantong",31.98,120.89,"封测"),
"688396.SS":("华润微 CR Micro","中国大陆","Wuxi",31.50,120.35,"功率IDM"),
"300661.SZ":("圣邦股份 SG Micro","中国大陆","Beijing",39.98,116.30,"模拟"),
"300782.SZ":("卓胜微 Maxscend","中国大陆","Wuxi",31.57,120.30,"射频"),
"688126.SS":("沪硅产业 NSIG","中国大陆","Shanghai",31.22,121.63,"硅片"),
"688072.SS":("拓荆科技 Piotech","中国大陆","Shenyang",41.80,123.43,"薄膜设备"),
"688120.SS":("华海清科 Hwatsing","中国大陆","Tianjin",39.09,117.21,"CMP设备"),
"688047.SS":("龙芯中科 Loongson","中国大陆","Beijing",39.91,116.40,"CPU"),
"603893.SS":("瑞芯微 Rockchip","中国大陆","Fuzhou",26.07,119.30,"AIoT SoC"),
"688099.SS":("晶晨股份 Amlogic","中国大陆","Shanghai",31.20,121.59,"多媒体SoC"),
"600745.SS":("闻泰科技 Wingtech","中国大陆","Jiaxing",30.75,120.76,"功率/ODM"),
"600703.SS":("三安光电 Sanan","中国大陆","Xiamen",24.48,118.09,"化合物半导体"),
"600460.SS":("士兰微 Silan","中国大陆","Hangzhou",30.27,120.16,"功率IDM"),
"300223.SZ":("北京君正 Ingenic","中国大陆","Beijing",39.98,116.31,"存储/MCU"),
# ===== 东南亚 =====
"0166.KL":("Inari Amertron","马来西亚","Penang",5.35,100.30,"封测"),
"558.SI":("UMS Integration","新加坡","Singapore",1.35,103.87,"设备零部件"),
}
with data_lock(BASE):
    rebuild_active_universe(BASE, C)
    C = active_universe_as_tuples(BASE, C)

HISTORY_STATUS_PATH = BASE / "data" / "history_status.json"
HISTORY_PROCESS_LOCK = try_process_lock(BASE, "history.lock")
if HISTORY_PROCESS_LOCK is None:
    print("history refresh skipped: already running")
    sys.exit(0)


def write_history_status(**fields):
    payload = read_json(HISTORY_STATUS_PATH, {})
    payload.update(fields)
    payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json_atomic(HISTORY_STATUS_PATH, payload)


write_history_status(
    status="running",
    started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    last_attempt=time.strftime("%Y-%m-%d %H:%M:%S"),
    message="历史窗口刷新运行中",
    last_error="",
)
_ORIGINAL_EXCEPTHOOK = sys.excepthook


def _history_exception_hook(exc_type, exc, traceback):
    write_history_status(
        status="error",
        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        message="历史窗口刷新失败，已保留原数据",
        last_error=f"{exc_type.__name__}: {exc}"[:1000],
    )
    _ORIGINAL_EXCEPTHOOK(exc_type, exc, traceback)


sys.excepthook = _history_exception_hook

from datetime import date, timedelta
TODAY = date.today()
WIN_START = (TODAY - timedelta(days=32)).isoformat()   # 滚动窗口：最近一个月
WIN_END = TODAY.isoformat()
START, END = (TODAY - timedelta(days=35)).isoformat(), (TODAY + timedelta(days=1)).isoformat()

tickers = list(C.keys())
print(f"公司数: {len(tickers)}")

# ---- 历史行情：Yahoo v8 chart API（与 fetch_live 同通道） ----
import datetime as _dt
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HIST_TIMEOUT = float(os.getenv("AICM_HIST_TIMEOUT", "10"))
HIST_WORKERS = max(1, int(os.getenv("AICM_HIST_WORKERS", "6")))
HIST_RETRY_WORKERS = max(1, int(os.getenv("AICM_HIST_RETRY_WORKERS", "3")))
HIST_RETRY_DELAY = float(os.getenv("AICM_HIST_RETRY_DELAY", "2.0"))
HIST_PAUSE = float(os.getenv("AICM_HIST_PAUSE", "0.03"))


def _chart_request(symbol, range_="3mo", interval="1d"):
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range={range_}&interval={interval}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
    })
    with urllib.request.urlopen(req, timeout=HIST_TIMEOUT) as response:
        payload = json.loads(response.read())
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(str(chart["error"])[:180])
    results = chart.get("result") or []
    if not results:
        raise ValueError("empty chart result")
    return results[0]


def fetch_chart_history(symbol):
    """返回 [(date_str, close, volume), ...]（按交易所本地时区换算日期）。"""
    result = _chart_request(symbol)
    meta = result.get("meta") or {}
    gmtoffset = int(meta.get("gmtoffset") or 0)
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    rows = []
    for i, stamp in enumerate(timestamps):
        close = closes[i] if i < len(closes) else None
        if stamp is None or close is None:
            continue
        volume = volumes[i] if i < len(volumes) else None
        day = _dt.datetime.utcfromtimestamp(int(stamp) + gmtoffset).strftime("%Y-%m-%d")
        rows.append((day, float(close), float(volume or 0)))
    # 同一天多条（罕见）保留最后一条
    dedup = {}
    for day, close, volume in rows:
        dedup[day] = (day, close, volume)
    return [dedup[k] for k in sorted(dedup)]


def fetch_histories(symbols):
    def run_batch(batch, workers):
        got, bad = {}, {}
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(batch)))) as pool:
            futures = {}
            for idx, sym in enumerate(batch):
                futures[pool.submit(fetch_chart_history, sym)] = sym
                if HIST_PAUSE and idx < len(batch) - 1:
                    time.sleep(HIST_PAUSE)
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    got[sym] = future.result()
                except Exception as exc:
                    bad[sym] = str(exc)[:140]
        return got, bad

    histories, failures = run_batch(symbols, HIST_WORKERS)
    retry = [s for s in symbols if s in failures]
    if retry:
        time.sleep(HIST_RETRY_DELAY)
        retry_got, retry_bad = run_batch(retry, HIST_RETRY_WORKERS)
        histories.update(retry_got)
        failures = {s: m for s, m in failures.items() if s not in retry_got}
        failures.update(retry_bad)
    return histories, failures


HISTORIES, HISTORY_FAILURES = fetch_histories(tickers)
for sym, reason in sorted(HISTORY_FAILURES.items()):
    print("history fail:", sym, reason)

# FX → USD（同 chart 通道）
FX = {"USD":1.0}
fx_map = {"TWD":"TWD=X","KRW":"KRW=X","JPY":"JPY=X","EUR":"EURUSD=X","CNY":"CNY=X","HKD":"HKD=X",
          "CHF":"CHF=X","NOK":"NOK=X","SEK":"SEK=X","SGD":"SGD=X","MYR":"MYR=X","ILS":"ILS=X","GBP":"GBPUSD=X",
          "SAR":"SAR=X","INR":"INR=X","AUD":"AUDUSD=X","CAD":"CAD=X","COP":"COP=X","ZAR":"ZAR=X"}
for cur, sym in fx_map.items():
    try:
        rows = fetch_chart_history(sym)
        v = float(rows[-1][1])
        FX[cur] = v if cur in ("EUR","GBP","AUD") else 1.0/v   # XXXUSD直接是USD价，其余是 USD->cur
    except Exception as e:
        print("FX fail", cur, e); FX[cur] = None
print("FX:", {k: round(v,4) if v else None for k,v in FX.items()})

def cur_of(t):
    if t.endswith((".TW",".TWO")): return "TWD"
    if t.endswith((".KS",".KQ")): return "KRW"
    if t.endswith(".T"): return "JPY"
    if t.endswith((".AS",".DE",".PA",".BR",".MC")): return "EUR"
    if t.endswith(".MI"): return "EUR"
    if t.endswith(".OL"): return "NOK"
    if t.endswith(".ST"): return "SEK"
    if t.endswith(".SW"): return "CHF"
    if t.endswith(".HK"): return "HKD"
    if t.endswith((".SS",".SZ",".BJ")): return "CNY"
    if t.endswith(".KL"): return "MYR"
    if t.endswith(".SI"): return "SGD"
    if t.endswith(".L"): return "GBP"
    if t.endswith(".SR"): return "SAR"
    if t.endswith(".NS"): return "INR"
    if t.endswith(".AX"): return "AUD"
    if t.endswith(".V"): return "CAD"
    if t.endswith(".CL"): return "COP"
    if t.endswith(".JO"): return "ZAR"
    return "USD"

def price_scale_of(t):
    return 0.01 if t.endswith(".JO") else 1.0

# 市值：yfinance fast_info 通道已被限流，无法稳定获取新市值。
# 结转上一份 semi_market.json 中的市值（十亿美元），拉不到的记 0，不阻塞主数据。
_prev_market = read_json(OUT_PATH, {})
PREV_MCAPS = {}
for _c in _prev_market.get("companies", []) if isinstance(_prev_market, dict) else []:
    try:
        PREV_MCAPS[_c["t"]] = float(_c.get("mcap") or 0)
    except Exception:
        continue

out = {"companies": [], "dates": [], "meta": {
    "window": f"{WIN_START} ~ {WIN_END}", "fetched": TODAY.isoformat(),
    "price_source": "yahoo_chart", "mcap_source": "carry_forward",
}}
all_dates = set()
for t in tickers:
    rows = HISTORIES.get(t)
    if not rows:
        print("no data:", t); continue
    rows = [r for r in rows if WIN_START <= r[0] <= WIN_END]
    if len(rows) < 5:
        print("too few rows:", t, len(rows)); continue
    cur = cur_of(t); fx = FX.get(cur) or 0
    name, country, city, lat, lon, seg = C[t]
    meta = enrich_company(t, name, country, city, seg)
    scale = price_scale_of(t)
    closes = [round(r[1] * scale, 4) for r in rows]
    dates = [r[0] for r in rows]
    all_dates.update(dates)
    vol_usd = [round(r[1] * scale * r[2] * fx / 1e6, 2) for r in rows]  # 百万美元
    mc_usd = PREV_MCAPS.get(t) or 0   # 十亿美元（结转）
    out["companies"].append({
        "t": t, "name": name, "country": country, "city": city, "lat": lat, "lon": lon, "seg": seg,
        **meta,
        "mcap": round(mc_usd, 2), "dates": dates, "close": closes, "volusd": vol_usd,
        "ret": round((closes[-1]/closes[0]-1)*100, 2),
    })

out["dates"] = sorted(all_dates)
if not out["companies"] or not out["dates"]:
    raise RuntimeError(
        f"采集结果为空，已停止写入，保留现有 {OUT_PATH}。"
        "请稍后重试，通常是 Yahoo/yfinance 限流或网络临时失败。"
    )

with data_lock(BASE):
    OUT_PATH.parent.mkdir(exist_ok=True)
    if OUT_PATH.exists():
        try:
            old = json.load(open(OUT_PATH))
            if old.get("companies") and old.get("dates"):
                backup = OUT_PATH.with_name(f"semi_market.bak_{time.strftime('%Y%m%d_%H%M%S')}.json")
                os.replace(OUT_PATH, backup)
                print("已备份旧主数据:", backup)
        except Exception as e:
            print("旧主数据备份跳过:", e)

    tmp_path = OUT_PATH.with_suffix(".json.tmp")
    json.dump(out, open(tmp_path, "w"), ensure_ascii=False)
    os.replace(tmp_path, OUT_PATH)
print(f"\n完成: {len(out['companies'])} 家公司, {len(out['dates'])} 个交易日")
print("月度涨幅 TOP5:", sorted([(c['name'], c['ret']) for c in out['companies']], key=lambda x:-x[1])[:5])
print("月度跌幅 TOP5:", sorted([(c['name'], c['ret']) for c in out['companies']], key=lambda x:x[1])[:5])
write_history_status(
    status="success",
    finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    last_success=time.strftime("%Y-%m-%d %H:%M:%S"),
    message="历史窗口刷新成功",
    last_error="",
    company_count=len(out["companies"]),
    date_count=len(out["dates"]),
)
