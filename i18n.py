"""Simple EN/ZH UI translations. Default language: English."""

from __future__ import annotations

from typing import Any

from flask import request, session

LANGS = ("en", "zh")
DEFAULT_LANG = "en"
SESSION_LANG_KEY = "ui_lang"

# English msgid → Chinese. English UI uses the msgid itself.
ZH: dict[str, str] = {
    # Nav
    "Home": "首页",
    "Stock Tracker": "个股分析",
    "Market Dashboard": "市场看板",
    "Watchlist": "观察列表",
    "News Score":
        "新闻得分",
    "News gate (Watchlist primary groups)":
        "新闻门槛（Watchlist 主信号组）",
    "Exception — My Watchlist: always load Financial and News when the ticker resolves (regular holdings).":
        "例外 —「我的自选」：只要代码能解析，就始终加载财报与新闻（常持股）。",
    "Signal → Financial Score → if Financial Pass Rate ≥ 60% (ok/known), fetch/analyze News (reuse fresh cache); else SKIPPED, News Score = 0, no News API. Financial ≥ 60% is only the News-analysis gate — not a buy condition. SKIPPED ≠ NEUTRAL (both score 0).":
        "信号 → 财报得分 → 若财报通过率 ≥60%（ok/known）则抓取/分析新闻（新鲜缓存可复用）；否则 SKIPPED，新闻得分=0，不调 News API。财报≥60% 仅为新闻分析门槛，不是买入条件。SKIPPED ≠ NEUTRAL（二者得分都是0）。",
    "Risk penalties: severe financial −0~15, volume dump −0~5, high vol/low liquidity −0~5, near earnings −0~15. News is not double-counted in risk (only ±5 News Score). Hover AI for breakdown. Green≥70 / yellow 40–69 / red <40. 63D Position and Admin-only Intrinsic Value / real MOS are excluded from AI Score V1.":
        "风险扣分：严重财务 −0~15、放量下跌 −0~5、高波动/低流动性 −0~5、临近财报 −0~15。新闻不在风险中重复扣分（仅 ±5 新闻得分）。悬停 AI 看明细。绿≥70 / 黄40–69 / 红<40。63日位置与管理员估值/真实 MOS 不计入 AI Score V1。",
    "Research": "研究中心",
    "Added: {tickers}": "已添加：{tickers}",
    "Already on list: {tickers}": "已在列表中：{tickers}",
    "Invalid: {tickers}": "无效代码：{tickers}",
    "No tickers to add": "没有可添加的股票代码",
    "Sign in to save {n} ticker(s) to My Watchlist": "请登录以将 {n} 只股票保存到「我的自选」",
    "Strong Stock Monitor": "研究中心",  # legacy msgid
    "Settings": "设置",
    "Order Requests": "订单请求",
    "Login": "登录",
    "Logout": "退出",
    "EN": "EN",
    "中文": "中文",
    # Common
    "Save": "保存",
    "Cancel": "取消",
    "Back to Watchlist": "返回 Watchlist",
    "Back to Dashboard": "返回 Dashboard",
    "Password": "密码",
    "New password": "新密码",
    "Confirm password": "确认密码",
    "Save & sign in": "保存并登录",
    "Sign in": "登录",
    "Drag horizontally to see more columns": "左右拖动查看更多列",
    # Login
    "Owner Login": "所有者登录",
    "Set owner password": "设置所有者密码",
    "First-time setup: choose a password only you know. Use it later to edit My Watchlist and view Est.Value / MOS / CLV (in development; hidden on the public site).":
        "首次使用：请设置仅你本人知道的登录密码。之后可用此密码修改「我的自选」，并查看 Est.Value / MOS / CLV（开发中，公开页不显示）。",
    "After signing in you can edit My Watchlist and Manual Alert, and view Est.Value / MOS / CLV. Public visitors do not see those three valuation columns.":
        "登录后可修改「我的自选」与人工提醒价，并查看 Est.Value / MOS / CLV。公开访客看不到这三列估值。",
    "Password must be at least 6 characters": "密码至少 6 位",
    "Passwords do not match": "两次密码不一致",
    "Password saved — you are signed in": "密码已设置并登录",
    "Signed in": "已登录",
    "Wrong password": "密码错误",
    "Signed out": "已退出登录",
    "Please sign in to edit My Watchlist": "请先登录后再修改我的自选",
    "My Watchlist updated": "已更新我的自选",
    "Removed from My Watchlist": "已从我的自选移除",
    # Settings
    "SMA period is configurable — change it below, then refresh prices on Market Dashboard.":
        "平均周期不固定 — 可在下面手动修改。改完后请到 Market Dashboard 重新「刷新行情」。",
    "SMA period (days)": "平均周期（SMA 天数）",
    "Rebound lookback (days vs recent low)": "反弹率回看天数（相对近期低点）",
    "Data source": "数据源",
    "Yahoo Finance (current)": "Yahoo Finance（当前）",
    "IBKR (future)": "IBKR（以后升级）",
    "Auto updates (Pacific Time)": "自动更新（太平洋时间）",
    "Universe / index members: refresh from Wikipedia one day per week.":
        "公司名 / 指数成分：每周一天从 Wikipedia 更新。",
    "Prices: every US trading day after the close, refresh all pools from Yahoo and sync Watchlist (including MANUAL).":
        "列表行情：每个交易日美股收盘后从 Yahoo 更新全部股池，并同步 Watchlist（含 MANUAL）。",
    "Default 13:15 Pacific ≈ 16:15 Eastern. You can also click “Refresh all prices + Watchlist” on Dashboard / Watchlist.":
        "默认 13:15 太平洋 ≈ 16:15 美东。也可在 Dashboard / Watchlist 点「刷新全部行情 + Watchlist」手动更新。",
    "Universe update · weekday": "公司名更新 · 星期",
    "Universe update · time": "公司名更新 · 时间",
    "Price update · weekday after close": "列表行情 · 工作日收盘后时间",
    "In-app scheduler:": "应用内调度：",
    "running": "运行中",
    "not running": "未运行",
    "Next:": "下次：",
    "Refresh all prices + Watchlist now": "立即刷新全部行情 + Watchlist",
    "Updating (may take a few minutes)…": "更新中（可能需数分钟）…",
    "Mon": "周一",
    "Tue": "周二",
    "Wed": "周三",
    "Thu": "周四",
    "Fri": "周五",
    "Sat": "周六",
    "Sun": "周日",
    # Dashboard
    "Refresh universe (weekly)": "更新股票池成分（每周）",
    "Refresh all prices + Watchlist": "刷新全部行情 + Watchlist",
    "Refresh this group only": "仅刷新本组",
    "Basic info": "基本信息",
    "Price / trend": "价格 / 趋势",
    "Range / momentum": "区间 / 动量",
    "Value reference": "价值参考",
    "Risk / events": "风险 / 事件",
    "Stock": "股票",
    "Industry": "行业",
    "Trend": "趋势",
    "Price": "现价",
    "Market Cap": "市值",
    "Mean (SMA)": "均值(SMA)",
    "Mean (SMA{n})": "均值(SMA{n})",
    "Day %": "当日%",
    "Dist. from mean %": "离均值%",
    "Rebound %": "反弹率",
    "Avg vol 20D": "20D均量",
    "Avg daily move %(63D)": "日均波动%(63D)",
    "Earnings date": "财报日",
    "No cached data for this group. Refresh universe weekly, then refresh all prices + Watchlist.":
        "本组还没有缓存数据。请先点「更新股票池成分（每周）」，再点「刷新全部行情 + Watchlist」。",
    # Watchlist tabs / actions
    "Oversold pullback": "超卖回调",
    "Target Ratio < 80%": "Target Ratio < 80%",
    "My Watchlist": "我的自选",
    "Temp": "临时",
    "Add to My Watchlist": "添加到我的自选",
    "Add tickers, comma-separated, e.g. AMD, SHOP.TO": "添加自选代码，逗号分隔，如 AMD, SHOP.TO",
    "Current list:": "当前自选：",
    "(empty)": "（空）",
    "Public visitors can browse My Watchlist quotes but cannot edit the list; Est / MOS / CLV require sign-in.":
        "公开访客可浏览我的自选行情，但不能修改名单；Est / MOS / CLV 需登录后可见。",
    "Add": "添加",
    "Clear": "清空",
    "Temp list holds up to {max} tickers (extras ignored); cleared when the browser closes. Now {n} / {max}.":
        "临时列表最多 {max} 个股票（超出的会被忽略）；关闭浏览器后自动清空。当前 {n} / {max}。",
    "Enter tickers, comma-separated, e.g. TSLA, AMD, SHOP.TO":
        "输入代码，逗号分隔，如 TSLA, AMD, SHOP.TO",
    "Score": "评分",
    "Investment value": "投资价值",
    "Manual alert": "人工关注",
    "SMA alerts": "SMA 提醒",
    "Default Alert": "默认提醒价",
    "Manual Alert": "人工提醒价",
    "Active Alert": "生效提醒价",
    "Alert Status": "提醒状态",
    "Reset": "重置",
    "Deep Alert (info)": "深度提醒（仅参考）",
    "SMA alerts: Default = SMA×0.95 (auto). Manual overrides Active until Reset. WATCH / ALERT / DEEP are research zones only — never auto-buy.":
        "我的自选提醒：Auto 🟡 WATCH = SMA 下方 5% · 🟢 DEEP = SMA 下方 10%。Manual 覆盖 Auto，直至重置。",
    "SMA alerts: Default = SMA×0.95 (auto). Manual overrides Active until Reset. WATCH = within 5% above Active; ALERT ≤ Active; DEEP ≤ SMA×0.90. Research only — never auto-buy.":
        "我的自选提醒：Auto 🟡 WATCH = SMA 下方 5% · 🟢 DEEP = SMA 下方 10%。Manual 覆盖 Auto，直至重置。",
    "My Watchlist SMA alerts: Default = SMA×0.95 (auto); Manual overrides Active until Reset. Dots on ticker: 🟡 WATCH = within 5% above Active (≤ Active×1.05); 🟢 ALERT = price ≤ Active; 🟢 DEEP = price ≤ SMA×0.90. Research only — never auto-buy.":
        "我的自选提醒：Auto 🟡 WATCH = SMA 下方 5% · 🟢 DEEP = SMA 下方 10%。Manual 覆盖 Auto，直至重置。",
    "My Watchlist Alerts": "我的自选提醒",
    "Auto: 🟡 WATCH = 5% below SMA · 🟢 DEEP = 10% below SMA":
        "Auto：🟡 WATCH = SMA 下方 5% · 🟢 DEEP = SMA 下方 10%",
    "Manual: Custom alert overrides Auto · 🟡 WATCH = within 5% above alert · 🟢 ALERT = alert reached · Remains active until reset":
        "Manual：自定义提醒覆盖 Auto · 🟡 WATCH = 手动提醒价上方 5% 内 · 🟢 ALERT = 已到达提醒价 · 持续有效直至重置",
    "Auto: 🟡 WATCH / 🟢 DEEP · Manual: 🟡 WATCH / 🟢 ALERT. Same as dots on ticker.":
        "Auto：🟡 WATCH / 🟢 DEEP · Manual：🟡 WATCH / 🟢 ALERT。与代码旁圆点同义。",
    "WATCH — 5% below SMA": "WATCH — SMA 下方 5%",
    "DEEP — 10% below SMA": "DEEP — SMA 下方 10%",
    "WATCH — within 5% above manual alert": "WATCH — 手动提醒价上方 5% 内",
    "ALERT — manual alert reached": "ALERT — 已到达手动提醒价",
    "No alert zone": "无提醒区",
    "Auto": "Auto",
    "Manual": "手动",
    "SMA-based until Manual is set": "基于 SMA，直到设置 Manual",
    "5% below SMA": "SMA 下方 5%",
    "10% below SMA": "SMA 下方 10%",
    "custom price overrides Auto until Reset": "自定义价格覆盖 Auto，直至重置",
    "within 5% above manual alert": "手动提醒价上方 5% 内",
    "price ≤ manual alert": "现价 ≤ 手动提醒价",
    "Dots next to ticker only — hover for status. Research zones only; never auto-buy.":
        "代码旁仅显示黄/绿点 — 悬停查看状态。仅为研究区，绝不会自动买入。",
    "Default Alert = SMA × 0.95. Updates whenever SMA refreshes. Not a buy signal.":
        "默认提醒价 = SMA × 0.95（Auto WATCH 阈值）。随 SMA 刷新而更新。不是买入信号。",
    "Manual Alert — your override. Stays fixed until you edit or Reset to Default.":
        "人工提醒价 — 覆盖 Auto。固定不变，直到你修改或「重置为默认」。",
    "Active Alert = Manual if set, else Default. Used for ALERT status.":
        "生效提醒价 = 有 Manual 用 Manual，否则用 Auto 默认（SMA×0.95）。",
    "Active Alert = Manual if set, else Auto Default (SMA×0.95).":
        "生效提醒价 = 有 Manual 用 Manual，否则用 Auto 默认（SMA×0.95）。",
    "WATCH ≤ Active×1.05 · ALERT ≤ Active · DEEP ≤ SMA×0.90. Research zones only — never auto-buy.":
        "Auto：🟡 WATCH / 🟢 DEEP · Manual：🟡 WATCH / 🟢 ALERT。仅为研究区，绝不自动买入。",
    "WATCH ≤ SMA · ALERT ≤ Active · DEEP ≤ SMA×0.90. Research zones only — never auto-buy.":
        "Auto：🟡 WATCH / 🟢 DEEP · Manual：🟡 WATCH / 🟢 ALERT。仅为研究区，绝不自动买入。",
    "🟡 WATCH ≤ Active×1.05 · 🟢 ALERT ≤ Active · 🟢 DEEP ≤ SMA×0.90. Same meaning as dots on ticker. Research only — never auto-buy.":
        "Auto：🟡 WATCH / 🟢 DEEP · Manual：🟡 WATCH / 🟢 ALERT。与代码旁圆点同义。仅为研究区，绝不自动买入。",
    "Manual Alert. Enter/blur to save; clear or Reset to use Default (SMA×0.95). Does not trigger trades.":
        "人工提醒价。回车/失焦保存；清空或点「重置」则回到 Auto（SMA×0.95）。不触发交易。",
    "Reset to Default — clear Manual; Active = current SMA × 0.95":
        "重置为默认 — 清除 Manual；回到 Auto（当前 SMA × 0.95）",
    "Active = Manual Alert": "生效 = Manual 提醒价",
    "Active = Default Alert (SMA × 0.95)": "生效 = Auto 默认（SMA × 0.95）",
    "DEEP — Price ≤ SMA × 0.90. Research zone only; not an auto BUY.":
        "DEEP — SMA 下方 10%。仅为研究区，不是自动买入。",
    "ALERT — Price ≤ Active Alert. Research zone only; not an auto BUY.":
        "ALERT — 已到达手动提醒价。仅为研究区，不是自动买入。",
    "WATCH — Price ≤ SMA. Research zone only; not an auto BUY.":
        "WATCH — SMA 下方 5%。仅为研究区，不是自动买入。",
    "WATCH — within 5% above Active Alert (≤ Active×1.05). Research zone only; not an auto BUY.":
        "WATCH — 手动提醒价上方 5% 内。仅为研究区，不是自动买入。",
    "🟢 DEEP — Price ≤ SMA × 0.90. Same as green dot / Alert Status. Research only; not an auto BUY.":
        "DEEP — SMA 下方 10%。",
    "🟢 ALERT — Price ≤ Active Alert. Same as green dot / Alert Status. Research only; not an auto BUY.":
        "ALERT — 已到达手动提醒价。",
    "🟡 WATCH — within 5% above Active Alert (≤ Active×1.05). Same as yellow dot / Alert Status. Research only; not an auto BUY.":
        "WATCH — 手动提醒价上方 5% 内。",
    "Above SMA — no alert zone": "无提醒区",
    "No 🟡/🟢 — price above WATCH band (above Active×1.05)": "无提醒区",
    "Distance": "距离",
    "Short-term SMA used for Default / Deep alerts": "用于默认/深度提醒的短期均线",
    "AUTO BLOCK": "禁止自动交易",
    "Blocked": "Blocked",
    "AUTO BLOCK — Knife Risk above Auto Trading threshold":
        "禁止自动交易 — Knife Risk 高于自动交易门槛",
    "Knife Risk 0–100 — downside velocity + relative weakness vs SPY/sector. Independent of AI Score. Not an oversold score.":
        "Knife Risk 0–100 — 下跌速度 + 相对弱势 + 10D/20D 趋势持续性。独立于 AI Score。不是超卖分数。",
    "Knife Risk — falling speed + relative weakness. Independent of AI Score.":
        "Knife Risk — 下跌速度 + 相对弱势 + 趋势持续性。独立于 AI Score。",
    "Knife Risk — Auto pool excludes Knife ≥ threshold":
        "Knife Risk — 自动交易池排除 Knife ≥ 门槛的股票",
    "Knife Risk ≥ 45 blocks Auto Trading (configurable). Watchlist / Research still show the name.":
        "Knife Risk ≥ 45 会禁止自动交易（可配置）。Watchlist / 研究中心仍可查看。",
    "Knife Risk (0–100) — independent of AI Score":
        "Knife Risk（0–100）— 独立于 AI Score",
    "Speed":
        "下跌速度",
    "5D / 3D decline, consecutive down days, acceleration":
        "5日/3日跌幅、连续下跌日、加速下行",
    "Relative weakness":
        "相对弱势",
    "5D vs SPY (~20) + sector ETF (~15)":
        "5日相对 SPY（约20）+ 行业 ETF（约15）",
    "Trend persistence":
        "趋势持续性",
    "10D / 20D log-price slope still falling":
        "10日/20日对数价格斜率仍在下行",
    "Volume confirm":
        "成交量确认",
    "optional: down day + high RVOL (tiny add-on)":
        "可选：下跌日 + 高 RVOL（小幅加分）",
    "Levels:":
        "等级：",
    "AUTO BLOCK:":
        "自动拦截：",
    "Knife Risk ≥ 45 blocks AI Auto Trading / Create Paper Orders (threshold configurable). Watchlist and Research still show the name. AI Score cannot override.":
        "Knife Risk ≥ 45 会禁止 AI 自动交易 / 创建纸上订单（门槛可配置）。Watchlist 与研究中心仍显示该股。AI Score 不能覆盖此规则。",
    "Not an oversold / price-location score. Does not use 63D Position, Dist. from SMA, Financial, or News. Hover Knife for component breakdown.":
        "不是超卖 / 价格位置分数。不使用 63日位置、距 SMA 距离、财报或新闻。悬停 Knife 可看分项明细。",
    "Rising Score (0–100) — independent of Knife Risk & AI Score":
        "Rising Score（0–100）— 独立于 Knife Risk 与 AI Score",
    "Rising Now entry (weak filter only):":
        "Rising Now 入选（故意放宽）：",
    "Up Days ≥ 3/5 · 5D Return ≥ +3%. Means “currently rising” — not how strong the rise is.":
        "上涨日 ≥ 3/5 · 5日涨幅 ≥ +3%。只表示「正在上涨」，不表示上涨有多强。",
    "Upside speed":
        "上涨速度",
    "light 5D/3D rise, consecutive up days, upside acceleration (5D already used for entry)":
        "轻度 5日/3日涨幅、连续上涨日、上行加速（5日已用于入选，此处权重较轻）",
    "Relative strength":
        "相对强势",
    "5D vs SPY (~10) + sector ETF (~8)":
        "5日相对 SPY（约10）+ 行业 ETF（约8）",
    "Uptrend persistence":
        "上涨趋势持续性",
    "10D / 20D log-price slope still rising (primary engine)":
        "10日/20日对数价格斜率仍在上行（主要引擎）",
    "Strong Up Count 20D":
        "20日强势上涨日数",
    "days with daily return ≥ +1.5%":
        "单日涨幅 ≥ +1.5% 的交易日数",
    "higher position in 63D range → higher Rising Score":
        "63日区间位置越高 → Rising Score 越高",
    "Independence:":
        "独立性：",
    "Rising Score ≠ 100 − Knife Risk. Same stock can be Rising 85 / Knife 15 (stable uptrend) or Rising 85 / Knife 70 (strong but volatile). Uses the same local daily_bars history as Knife — no separate download.":
        "Rising Score ≠ 100 − Knife Risk。同一只股票可以是 Rising 85 / Knife 15（稳健上涨），也可以是 Rising 85 / Knife 70（强但波动大）。与 Knife 共用本地 daily_bars，不另下历史。",
    "Does not remove Knife BLOCK / WATCH / PASS. High Rising Score can still be AUTO BLOCKED by Knife Risk. Shown on Research → Rising Now (sortable); hover Rising for component breakdown.":
        "不取消 Knife 的 BLOCK / WATCH / PASS。高 Rising Score 仍可能因 Knife Risk 被自动拦截。显示于 Research → Rising Now（可排序）；悬停 Rising 可看分项。",
    "Up Days ≥ 3/5 · 5D Return ≥ +3% (weak entry only). Rising Score 0–100 then ranks uptrend strength/persistence (Speed 17 · Rel 18 · 10D/20D Trend 35 · Strong Up 20D 12 · 63D Pos 18). Independent of Knife — not 100−Knife. High Rising can still be Knife-BLOCKED. Research only; no retention; not a Buy signal.":
        "上涨日 ≥ 3/5 · 5日涨幅 ≥ +3%（入选故意放宽）。随后 Rising Score 0–100 衡量上涨强度/持续性（速度17 · 相对18 · 10/20日趋势35 · 20日强涨日12 · 63日位置18）。独立于 Knife，不是 100−Knife。高 Rising 仍可能被 Knife 拦截。仅研究用；无留存；非买入信号。",
    "Rising Score":
        "Rising Score",
    "Weak entry filter only. Rising Score ranks how strong / persistent the rise is (independent of Knife Risk).":
        "入选条件故意放宽。Rising Score 衡量上涨强度与持续性（独立于 Knife Risk）。",
    "Rising Score 0–100 — strength & persistence of the uptrend (not 100−Knife).":
        "Rising Score 0–100 — 上涨强度与持续性（不是 100−Knife）。",
    "Knife Risk — falling speed + relative weakness. Independent of Rising Score.":
        "Knife Risk — 下跌速度与相对弱势。独立于 Rising Score。",
    "Pool": "所属股池",
    "No data yet. (Setup/pullback need a Market Dashboard price refresh; My Watchlist / Temp fetch live.)":
        "暂无数据。（超卖/强势回调需先到 Market Dashboard 刷新行情；我的自选/临时会实时抓取。）",
    "Ticker not found (bad symbol or delisted).":
        "未找到该股票的行情数据（可能代码有误或已退市）。",
    "Code guide (AI / fundamentals / news / valuation)":
        "代码说明（AI 打分 / 财报 / 新闻 / 估值）",
    "Financial": "财报",
    "Fundamentals": "财报",
    "News": "新闻",
    "Valuation": "投资价值",
    "Est.Value / MOS% / CLV are visible only after owner sign-in (methods still in development; hidden publicly).":
        "Est.Value / MOS% / CLV 仅所有者登录后可见（估值方法仍在开发中，公开页不展示）。",
    "Sign in": "登录",
    "Fund cache coverage:": "财报缓存覆盖：",
    "assembled": "装配",
    "News shown only when Financial Pass Rate ≥ 60% (ok/known); below threshold news is — · Est / MOS / CLV / AI blank on this tab.":
        "新闻仅在财报通过率 ≥ 60%（通过/已知）时显示；低于阈值新闻为 — · Est / MOS / CLV / AI 本页留空。",
    # Watchlist tab descriptions (short)
    "desc_setup":
        "Merged former oversold + pullback. Auto: Dist% < -10%. Trend may be UP / MIXED / DOWN; priority UP > MIXED > DOWN, then deepest Dist%. Est/MOS/CLV may be empty; Target Ratio still shown when available. 63D range is observational only.",
    "desc_setup_zh":
        "原「超卖建议」与「强势回调」已合并。自动生成：离均值% < -10%。Trend 可为 UP / MIXED / DOWN；优先级 UP > MIXED > DOWN，同趋势内按离均值% 从低到高。Est.Value / MOS / CLV 可为空；Target Ratio 仍显示。63D 区间仅观察。",
    "desc_low_target":
        "Auto: Target Ratio = price ÷ 1Y Target < 0.80, sorted low→high. 63D Position not filtered. Fund from shared cache only; news only when Financial Pass Rate ≥ 60%. No DCF/CLV/MOS/AI on this tab.",
    "desc_low_target_zh":
        "自动生成：Target Ratio = 现价 ÷ 1Y Target < 0.80，按 Ratio 从低到高。不再排除 63D Position。财报只读共享缓存；新闻仅在财报通过率 ≥ 60% 时读取。本页不跑 DCF/CLV/MOS/AI。",
    "desc_low_63d":
        "Auto: 63D Position% < 25%, sorted low→high (near 63D low first). Fund from shared cache; news only when Financial Pass Rate ≥ 60%. No DCF/CLV/MOS/AI on this tab.",
    "desc_low_63d_zh":
        "自动生成：63日位置% < 25%，按位置从低到高（越靠近 63 日低点越靠前）。财报只读共享缓存；新闻仅在财报通过率 ≥ 60% 时读取。本页不跑 DCF/CLV/MOS/AI。",
    "Up Days ≥ 3/5 · 5D Return ≥ +3%. Dynamic daily group; no retention. Independent of Strong Day / COUNT20.":
        "近5日上涨 ≥ 3天 · 5日累计涨幅 ≥ +3%。每日动态分组，无留存；独立于 Strong Day / COUNT20。",
    "Match ≥ 2 across Oversold / Target <80% / 63D Low / Rising Now. Strong is a separate indicator. No retention.":
        "在超卖 / Target <80% / 63日低位 / 正在上涨中 Match ≥ 2。Strong 为单独指示，不计入 Match。无留存。",
    "No Rising Now stocks under the current rules.":
        "当前规则下暂无「正在上涨」股票。",
    "No Multi-Signal stocks under the current rules.":
        "当前规则下暂无「多重信号」股票。",
    "Dynamic daily group; no retention. Independent of Strong Day / COUNT20.":
        "每日动态分组，无留存；独立于 Strong Day / COUNT20。",
    "Research aggregation of all signal groups with Financial / News / COUNT20. Human My Watchlist and Trade Candidate flags. AI Score unchanged.":
        "聚合各信号组并补齐财报 / 新闻 / COUNT20。人工「我的自选」与 Trade Candidate 标记。不改 AI Score。",
    "Unified candidate analysis from LeiBot signal groups.":
        "汇总 LeiBot 各信号组的统一候选分析。",
    "Research identifies strong stocks for monitoring — not immediate buying. The strategy is to wait for a suitable pullback and reassess price, risk and signals before considering entry.":
        "研究中心用于发现并监控强势股——不是立即买入信号。策略是等待合适回调，再重新评估价格、风险与信号，然后才考虑是否入场。",
    "Research selects candidates for the Long-Term Watchlist — not for immediate buying. Wait for the price to pull back toward its lowest range before considering an entry.":
        "研究中心筛选强势候选进入长期观察列表——不是立即买入。等待价格回落到低位区间后再考虑入场。",
    "Research selects strong candidates for the Long-Term Watchlist — not for immediate buying. Wait for the price to pull back toward its lowest range before considering an entry.":
        "研究中心筛选强势候选进入长期观察列表——不是立即买入。等待价格回落到低位区间后再考虑入场。",
    "Strong first · Wait for pullback · Reassess before entry":
        "先找强势 · 等待回调 · 入场前再评估",
    "Candidate Analysis combines valuation, position, momentum, strength and financial signals. Candidates are for research and monitoring; selection does not mean Buy.":
        "候选分析汇总估值、仓位、动量、强势与财报信号。候选仅供研究与监控；入选不等于买入。",
    "Add to My Watchlist or mark Trade Candidate manually — nothing auto-selects for AI Trading.":
        "请手动加入「我的自选」或标记 Trade Candidate——不会自动进入 AI Trading。",
    "Deduplicated research universe combining valuation, position, momentum, strength and financial signals. Manual My Watchlist / Trade Candidate flags only — nothing auto-selects for AI Trading.":
        "去重研究宇宙：汇总估值、仓位、动量、强势与财报信号。仅手动「我的自选」/ Trade Candidate 标记——不会自动进入 AI Trading。",
    "Deduplicated research universe combining valuation, position, momentum, strength and financial signals. Add to My Watchlist or mark Trade Candidate manually — nothing auto-selects for AI Trading.":
        "去重研究宇宙：汇总估值、仓位、动量、强势与财报信号。手动加入「我的自选」或标记 Trade Candidate——不会自动进入 AI Trading。",
    "Workflow: Watchlist → Research → My Watchlist / Trade Candidate → AI Trading. Signal ≠ research decision ≠ trade decision.":
        "流程：观察列表 → 研究中心 → 我的自选 / Trade Candidate → AI Trading。信号 ≠ 研究决策 ≠ 交易决策。",
    "Workflow: Find strong stocks → Monitor → Wait for pullback → Reassess → Consider entry. Signal ≠ research decision ≠ trade decision. Nothing auto-selects for AI Trading.":
        "流程：发现强势股 → 监控 → 等待回调 → 重新评估 → 再考虑入场。信号 ≠ 研究决策 ≠ 交易决策。不会自动选入 AI Trading。",
    "Stocks meeting the Strong Day Position threshold that day — research candidates for monitoring, not immediate Buy signals. Column lengths differ by day.":
        "当日达到 Strong Day 仓位阈值的股票——供监控的研究候选，不是立即买入信号。各列长度因日而异。",
    "Historical frequency over the latest 20 trading days. Research ranking only; not a Buy list. Current Position may be below the Strong Day threshold.":
        "近 20 个交易日的历史出现频率。仅为研究排名，不是买入清单。当前仓位可能低于 Strong Day 阈值。",
    "COUNT ≥ threshold qualifies; retain N trading days after last qualify. For monitoring pullbacks — not automatic Buy. Renew resets retention.":
        "COUNT ≥ 阈值入选；末次达标后再留存 N 个交易日。用于监控回调——不是自动买入。续期会重置留存。",
    "Up Days ≥ 3/5 · 5D Return ≥ +3%. Short-term momentum research group; not a Buy signal. No retention.":
        "上涨天数 ≥ 3/5 · 5日涨幅 ≥ +3%。短期动量研究分组；不是买入信号。无留存。",
    "Up Days ≥ 3/5 · 5D Return ≥ +3%. Dynamic daily group; no retention. Research candidates only — not a Buy signal.":
        "上涨天数 ≥ 3/5 · 5日涨幅 ≥ +3%。每日动态分组，无留存。仅为研究候选——不是买入信号。",
    "Match ≥ 2 across Oversold / Target &lt;80% / 63D Low / Rising Now. Strong is a separate indicator. Research candidates only — not a Buy signal.":
        "在超卖 / Target <80% / 63D 低位 / 正在上涨 中匹配 ≥2。Strong 为独立指示。仅为研究候选——不是买入信号。",
    "Match ≥ 2 across Oversold / Target &lt;80% / 63D Low / Rising Now. Overlap research screen only — not a Buy signal. Strong is a separate indicator.":
        "在超卖 / Target <80% / 63D 低位 / 正在上涨 中匹配 ≥2。仅为重叠研究筛选——不是买入信号。Strong 为独立指示。",
    "Match ≥ 2 across Oversold / Target &lt;80% / 63D Low / Rising Now. Strong is a separate indicator.":
        "在超卖 / Target <80% / 63D 低位 / 正在上涨 中匹配 ≥2。Strong 为独立指示。",
    "Up Days ≥ 3/5 · 5D Return ≥ +3%. Dynamic daily group; no retention.":
        "上涨天数 ≥ 3/5 · 5日涨幅 ≥ +3%。每日动态分组，无留存。",
    "Please sign in to manage Settings":
        "请先登录后再管理设置",
    "Please sign in to change Settings":
        "请先登录后再修改设置",
    "Please sign in to refresh all prices":
        "请先登录后再刷新全部行情",
    "Public view — Settings are read-only. Sign in as Admin to change values.":
        "公开只读视图。登录 Admin 后才能修改设置。",
    "Financial Score":
        "财报得分",
    "AI Score":
        "AI 得分",
    "Strong Retention":
        "强势留存",
    "No candidates yet. Refresh Market Dashboard / Research first.":
        "暂无候选。请先刷新市场看板 / 研究中心。",
    "Research action failed: {exc}":
        "研究中心操作失败：{exc}",
    "Strong Stock Monitor action failed: {exc}":
        "研究中心操作失败：{exc}",
    "Rising Now":
        "正在上涨",
    "Multi-Signal":
        "多重信号",
    "Candidate Analysis":
        "候选分析",
    "Research layer above LeiBot signal groups. Deduplicated candidates with Financial / News / Strong COUNT20 completed from cache. Add to My Watchlist manually; Trade Candidate is a separate human flag for the AI Trading Watchlist. AI Score formula is unchanged.":
        "位于各信号分组之上的研究层：去重候选，并用缓存补齐财报 / 新闻 / Strong COUNT20。手动加入「我的自选」；Trade Candidate 是给 AI Trading Watchlist 的独立人工标记。本任务不改 AI Score 公式。",
    "All Candidates":
        "全部候选",
    "Refresh Candidate Analysis":
        "刷新候选分析",
    "Fill missing Financial/News from cache/fetch for current candidates (bounded)":
        "为当前候选补齐缺失的财报/新闻（优先缓存，有界批量）",
    "Strong COUNT20":
        "Strong COUNT20",
    "Strong Status":
        "Strong 状态",
    "Signals":
        "信号",
    "Oversold pullback":
        "超卖回调",
    "Target Ratio < 80%":
        "Target Ratio < 80%",
    "63D Position < 25%":
        "63日位置 < 25%",
    "Multi-Signal 2/4":
        "多重信号 2/4",
    "Multi-Signal 3/4":
        "多重信号 3/4",
    "Multi-Signal 4/4":
        "多重信号 4/4",
    "Strong Watchlist":
        "Strong 观察",
    "Strong Retention":
        "Strong 留存",
    "Trade Candidate":
        "交易候选",
    "Trade Candidates":
        "交易候选",
    "Trade":
        "交易",
    "Add":
        "添加",
    "Marked as Trade Candidate":
        "已标记为交易候选",
    "Removed Trade Candidate flag":
        "已取消交易候选标记",
    "Please sign in to manage Candidate Analysis":
        "请登录后管理候选分析",
    "Candidate Analysis action failed: {exc}":
        "候选分析操作失败：{exc}",
    "Candidate research refreshed: fund ok_new {f} · news ok_new {n} (bounded batch)":
        "候选研究已刷新：财报新增 {f} · 新闻新增 {n}（有界批量）",
    "No candidates yet. Refresh Market Dashboard / Strong Monitor first.":
        "暂无候选。请先刷新 Market Dashboard / Strong Monitor。",
    "Financial 6/6":
        "财报 6/6",
    "Financial ≥5/6":
        "财报 ≥5/6",
    "63D Low":
        "63日低位",
    "Rising":
        "上涨",
    "AI Trading Watchlist — Top 10":
        "AI Trading Watchlist — Top 10",
    "AI Discovery":
        "AI Discovery",
    "AI Discovery Pool":
        "AI Discovery 候选池",
    "Broad Discovery + Official 5×5 Radar":
        "Broad Discovery + 官方 5×5 雷达",
    "Existing Broad Discovery is kept. Official channels each add Top 5. Shared dedupe → one Discovery table. Source tags: BROAD / USASPENDING / DOD / SEC / FDA / GOV_DISCLOSURE.":
        "保留现有 Broad Discovery。官方通道各加 Top 5。共用去重 → 同一 Discovery 表。来源标签：BROAD / USASPENDING / DOD / SEC / FDA / GOV_DISCLOSURE。",
    "Broad":
        "Broad",
    "Discovery Radar":
        "Discovery 雷达",
    "Broad Discovery":
        "Broad Discovery",
    "Pool":
        "候选池",
    "Open AI Discovery":
        "打开 AI Discovery",
    "Gov Disclosure":
        "政府披露",
    "Today admitted":
        "今日入池",
    "Source":
        "来源",
    "N":
        "条数",
    "Discovery: Broad {b} · USA {u} · DoD {d} · SEC {s} · FDA {f} · Gov {g} · raw {r} · today {t} · unresolved {x}":
        "Discovery：Broad {b} · USA {u} · DoD {d} · SEC {s} · FDA {f} · 披露 {g} · 原始 {r} · 今日 {t} · 未解析 {x}",
    "Five independent discovery channels":
        "五个独立发现通道",
    "Each channel contributes its own Top 5 (max 25 raw) → cross-source dedupe → ticker resolve → Event Score ≥ threshold → pool. No trading-gate changes.":
        "每通道各自 Top 5（最多 25 条原始）→ 跨源去重 → 解析代码 → Event Score ≥ 门槛 → 入池。不改交易阀门。",
    "Gov Transactions":
        "政府披露交易",
    "Raw":
        "原始合计",
    "Event ≥70":
        "事件 ≥70",
    "AI Discovery channels: USA {u} · DoD {d} · SEC {s} · FDA {f} · GovTx {g} · raw {r} · unique {q} · ≥70 {a} · unresolved {x}":
        "AI Discovery 通道：USA {u} · DoD {d} · SEC {s} · FDA {f} · 披露 {g} · 原始 {r} · 去重后 {q} · ≥70 {a} · 未解析 {x}",
    "External thematic news scan":
        "外部主题新闻扫描",
    "Google News themes discover material positive events across the broader market (not limited to Watchlist / Research).":
        "通过 Google News 主题扫描更广泛市场的重大利好（不限于自选 / Research）。",
    "Deduplicate underlying events → resolve ticker → Event Score ≥ threshold → store all qualifiers (no Top-N yet).":
        "按底层事件去重 → 解析代码 → Event Score ≥ 门槛 → 全部入库展示（暂无 Top-N）。",
    "Does not add to My Watchlist. Research / trading gates unchanged. High scores prefer primary-like sources when present.":
        "不会加入「我的自选」。Research / 交易阀门不变。高分优先参考一手/可靠来源（若有）。",
    "Qualifying Events":
        "达标事件",
    "Unique Stocks":
        "独立股票数",
    "Minimum Event Score":
        "最低 Event Score",
    "Apply":
        "应用",
    "Display filter only — does not delete stored events. Try 60 / 70 / 75 / 80 / 85.":
        "仅显示过滤，不删除已存事件。可试 60 / 70 / 75 / 80 / 85。",
    "No qualifying Discovery events at the current minimum Event Score. Run external thematic harvest, lower the threshold, or add an event manually.":
        "当前最低 Event Score 下无达标事件。请运行外部主题采集、降低门槛，或手动添加。",
    "Unresolved (ticker not confirmed)":
        "未解析（代码未确认）",
    "No unresolved headlines. Events without confident ticker resolution stay here — not in the tradable pool.":
        "暂无未解析标题。代码置信不足的事件留在此处，不进入可交易池。",
    "Note":
        "备注",
    "Min Event Score {score} · Qualifying Events {e} · Unique Stocks {s}":
        "最低 Event Score {score} · 达标事件 {e} · 独立股票 {s}",
    "AI Discovery: scanned {sc} · events +{e} · unresolved {u} · analyzed {a} · orders {n}":
        "AI Discovery：扫描 {sc} · 事件 +{e} · 未解析 {u} · 已分析 {a} · 订单 {n}",
    "News discovers the stock":
        "新闻发现股票",
    "Major positive events → Event Score → analyze with existing Financial / AI Score / Knife / price gates → optional auto Paper Order.":
        "重大利好事件 → Event Score → 复用现有财报 / AI Score / Knife / 价格阀门分析 → 可选自动纸上订单。",
    "Does not add to My Watchlist. Research thresholds unchanged. Knife AUTO BLOCK still applies.":
        "不会加入「我的自选」。Research 门槛不变。Knife 自动拦截仍然生效。",
    "Discovered":
        "已发现",
    "Discovery Alpha":
        "Discovery Alpha（发现阿尔法）",
    "Trading Alpha":
        "Trading Alpha（交易阿尔法）",
    "News Trading":
        "News Trading（新闻交易）",
    "Return":
        "收益",
    "Trades":
        "笔数",
    "AI_DISCOVERY source only · Total P&L = Realized + Unrealized":
        "仅 AI_DISCOVERY 来源 · 总盈亏 = 已实现 + 未实现",
    "Code Guide":
        "Code Guide（规则说明）",
    "Official 5×5 Radar":
        "Official 5×5 Radar（官方雷达）",
    "Scoring & pool rules":
        "评分与入池规则",
    "News labels: 🟢 POSITIVE / 🔴 NEGATIVE / ⚪ NEUTRAL":
        "新闻标注：🟢 利好 / 🔴 利空 / ⚪ 中性",
    "POSITIVE":
        "利好",
    "NEGATIVE":
        "利空",
    "NEUTRAL":
        "中性",
    "Event Score = materiality of the underlying event (not summed across headlines). Default display filter ≥ 70.":
        "Event Score = 底层事件实质影响（不按标题加总）。默认显示门槛 ≥ 70。",
    "Broad Discovery: thematic external news. Official 5×5: USAspending / DoD / SEC / FDA / Gov Disclosure — Top 5 each, then shared dedupe.":
        "Broad Discovery：外部主题新闻。Official 5×5：USAspending / DoD / SEC / FDA / 政府披露 — 各 Top 5，再共享去重。",
    "Unresolved tickers stay out of Priority and Trading until confidently resolved.":
        "未可靠解析代码前，不进 Priority / 交易。",
    "News Trading P&L = Realized + Unrealized for source AI_DISCOVERY only.":
        "News Trading 盈亏 = 已实现 + 未实现（仅来源 AI_DISCOVERY）。",
    "News Auto Trading method":
        "新闻自动 Trading 方法",
    "Discover → resolve ticker confidently → store unique underlying event (ticker + category + period).":
        "发现 → 高置信解析代码 → 按底层事件入库（代码 + 类别 + 期间，去重）。",
    "Analyze with existing Financial / AI Score / Knife / price-location gates (same as Paper Auto Trading).":
        "用现有财报 / AI Score / Knife / 价格位置阀门分析（与纸上 Auto Trading 相同）。",
    "Auto TRADE_CANDIDATE only if ALL pass: not 🔴 Negative · event is recent · Event Score ≥ 70 · AI Score ≥ 45 · price location OR gate · Knife below AUTO BLOCK (default ≥ 45 blocks).":
        "全部通过才自动 TRADE_CANDIDATE：非 🔴 Negative · 事件仍新 · Event Score ≥ 70 · AI Score ≥ 45 · 价格位置 OR 门槛 · Knife 低于 AUTO BLOCK（默认 ≥ 45 拦截）。",
    "Price location OR (any one): Dist from SMA25 ≤ −20% · or Target Ratio ≤ 70% · or 63D Position ≤ 10%.":
        "价格位置 OR（任一）：距 SMA25 ≤ −20% · 或 Target Ratio ≤ 70% · 或 63D 位置 ≤ 10%。",
    "Politician-purchase clues are discovery-only — never auto-ordered.":
        "政客买入线索仅作发现，永不自动下单。",
    "Paper order source = AI_DISCOVERY. At most one order per underlying event. Cash + trading limit apply. Default Stop −5% / Take +10% (Admin settings).":
        "纸上订单来源 = AI_DISCOVERY。同一底层事件最多一单。受现金与交易额度限制。默认止损 −5% / 止盈 +10%（管理员设置）。",
    "🔴 Negative / stale / Knife-blocked / price-fail → WATCH or AUTO_BLOCK — never Priority, never auto trade.":
        "🔴 Negative / 过期 / Knife 拦截 / 价格未达标 → WATCH 或 AUTO_BLOCK — 不进 Priority，不自动交易。",
    "Radar":
        "雷达",
    "Scores":
        "分数",
    "Alpha":
        "Alpha",
    "Actions":
        "操作",
    "Harvest":
        "采集",
    "forward returns, all events":
        "前瞻收益，全部事件",
    "Good":
        "好",
    "Medium":
        "中",
    "Bad":
        "差",
    "Knife":
        "Knife",
    "BROAD DISCOVER":
        "BROAD DISCOVER",
    "OFFICIAL 5×5 RADAR":
        "OFFICIAL 5×5 RADAR",
    "News":
        "新闻",
    "好":
        "好",
    "中":
        "中",
    "差":
        "差",
    "Official 5×5 is already in the pool — use tabs to view separately. Shared dedupe may list the same ticker in both when tags overlap.":
        "Official 5×5 已在同一池中 — 用 TAB 分开查看。共享去重后，双标签事件可能两边都出现。",
    "No Broad Discovery rows yet. Run Harvest + Analyze only (combined Broad + Official). Official rows are under the right tab.":
        "暂无 Broad Discovery。请运行 Harvest + Analyze only（Broad + Official 合并采集）。Official 在右侧 TAB。",
    "No Official 5×5 Radar rows at the current minimum Event Score.":
        "当前最低 Event Score 下无 Official 5×5 Radar 行。",
    "Unresolved":
        "未解析",
    "Resolved Today":
        "今日已解析",
    "Not in Priority / Trading until ticker is confidently resolved.":
        "代码未可靠解析前，不进入 Priority / 交易。",
    "Forward returns for every unique event (including WATCH / not traded). Separate from Paper Trading P&L.":
        "对每一个独立事件计算前瞻收益（含 WATCH / 未交易）。与纸上交易盈亏分开统计。",    "Unavailable periods show — until enough trading days have elapsed.":
        "未满观察期显示 —，不以 0% 填充。",
    "Closed Paper Trades with source AI_DISCOVERY only.":
        "仅统计来源为 AI_DISCOVERY 的已平仓纸上交易。",
    "Unique Events":
        "独立事件",
    "Trade Candidates":
        "交易候选",
    "Traded":
        "已下单",
    "5D Avg Return":
        "5日平均收益",
    "20D Avg Return":
        "20日平均收益",
    "63D Avg Return":
        "63日平均收益",
    "20D Avg vs SPY":
        "20日相对SPY",
    "63D Avg vs SPY":
        "63日相对SPY",
    "Period":
        "期间",
    "Src":
        "来源数",
    "Disc. Px":
        "发现价",
    "5D":
        "5日",
    "20D":
        "20日",
    "63D":
        "63日",
    "Supporting sources":
        "支持来源",
    "Traded (Discovery)":
        "已交易（Discovery）",
    "Open Discovery":
        "Discovery 持仓",
    "Run AI Discovery + Auto Orders":
        "运行 AI Discovery + 自动下单",
    "Harvest + Analyze only":
        "仅采集 + 分析",
    "Refreshes Broad + Official Discovery (not AI Candidates).":
        "刷新 Broad + Official Discovery（不是 AI Candidates）。",
    "Create Discovery Orders":
        "创建 Discovery 订单",
    "Major positive event summary (contract / FDA / guidance…)":
        "重大利好事件摘要（合同 / FDA / 上调指引…）",
    "Add Discovery Event":
        "添加 Discovery 事件",
    "No AI Discovery candidates yet. Run Harvest from shared news cache, or add a major event manually.":
        "暂无 AI Discovery 候选。请运行采集（共享新闻缓存），或手动添加重大事件。",
    "Event Score":
        "事件分",
    "Category":
        "类别",
    "Event":
        "事件",
    "Block / Note":
        "拦截 / 备注",
    "Analyze":
        "分析",
    "AI Discovery: events +{e} · analyzed {a} · orders {n}":
        "AI Discovery：事件 +{e} · 已分析 {a} · 订单 {n}",
    "Discovery event added: {ticker} · Event Score {score}":
        "已添加 Discovery 事件：{ticker} · Event Score {score}",
    "Analyzed {ticker}: {status}":
        "已分析 {ticker}：{status}",
    "Discovery paper orders: created {n} · skipped {s}":
        "Discovery 纸上订单：创建 {n} · 跳过 {s}",
    "Trade Guide":
        "Trade Guide（交易说明）",
    "Auto entry parameters":
        "自动入场参数",
    "Auto entry parameters (new)":
        "自动入场参数（新阀门）",
    "Price-location: ANY ONE of the three (OR). Knife is a separate hard block.":
        "价格位置：三项任一通过即可（OR）。Knife 仍为独立硬拦截。",
    "or":
        "或",
    "then":
        "然后",
    "Dist. from SMA25":
        "距 SMA25",
    "Knife Risk AUTO BLOCK":
        "Knife Risk 自动拦截",
    "Max slots":
        "最多名额",
    "no backfill":
        "不凑数",
    "Research Watchlist unchanged:":
        "Research Watchlist 不变：",
    "0 pass → NO TRADE":
        "0 只通过 → NO TRADE",
    "Auto gates":
        "自动阀门",
    "Ranked by AI Score; Priority Buy ⭐ only reorders allocation. Create Paper Orders is manual. Rising Now / 5D are timing references only.":
        "按 AI Score 排序；优先买入 ⭐ 仅调整仓位顺序。创建纸上订单仍需手动。正在上涨 / 5日仅为时机参考。",
    "Auto entry is stricter than Research: Dist. from SMA25 ≤ −20%, Target Ratio ≤ 70%, 63D Position ≤ 10%, then Knife Risk AUTO BLOCK. Research Watchlist keeps Dist < −10% / Target < 80% / 63D < 25%. Top 10 is a maximum — never backfill. 0 pass → NO TRADE. Ranked by AI Score; Priority Buy ⭐ only reorders allocation. Create Paper Orders is manual.":
        "自动入场严于 Research：距 SMA25 ≤ −20%、Target Ratio ≤ 70%、63日位置 ≤ 10%，再过 Knife Risk 自动拦截。Research Watchlist 仍为 Dist < −10% / Target < 80% / 63D < 25%。Top 10 是上限，绝不凑数。0 只通过 → NO TRADE。按 AI Score 排序；优先买入 ⭐ 仅调整仓位顺序。创建纸上订单仍需手动。",
    "NO TRADE":
        "NO TRADE（无交易）",
    "No names passed Auto price-location + Knife gates. Research filters are unchanged — wait for a better price, or refresh AI Candidates after prices update.":
        "没有标的通过自动交易的价格位置 + Knife 门槛。Research 筛选不变 — 等待更好价格，或行情更新后刷新 AI Candidates。",
    "max":
        "上限",
    "quality over fill":
        "质量优先，不凑满",
    "Strict paper-trading list: Oversold + Target Ratio < 80% + 63D Position < 25% (low position / quality screens), ranked by existing AI Score. Rising Now / 5D metrics are timing references only and do not change AI Score. Priority ⭐ and Trade Candidate ★ are human flags. Orders are not created until you click Create Paper Orders. Research more names on Candidate Analysis → My Watchlist → Trade Candidate.":
        "严格纸上交易列表：超卖 + Target Ratio <80% + 63日位置 <25%（低位/质量筛），按现有 AI Score 排序。正在上涨 / 5日指标仅为时机参考，不改 AI Score。Priority ⭐ 与 Trade Candidate ★ 为人工标记。点击 Create Paper Orders 才会建仓。更多研究请走：候选分析 → 我的自选 → Trade Candidate。",
    "No Trade Candidates yet. Mark them on Candidate Analysis (separate from My Watchlist).":
        "尚无交易候选。请在候选分析中标记（与我的自选分开）。",
    "desc_rising_now":
        "Independent dynamic group: Up Days ≥ 3 of latest 5 trading days AND 5D Total Return ≥ +3%. No 63D Position filter, no retention. AI from shared cache when available; Financial / News not shown on this V1 tab.",
    "desc_rising_now_zh":
        "独立动态分组：近5个交易日上涨天数 ≥ 3 且 5日累计涨幅 ≥ +3%。不按 63日位置筛选，无留存期。AI 仅用已有缓存（有则显示）；本 V1 页不展示财报 / 新闻。",
    "desc_multi_signal":
        "Aggregation only: Match Count = how many of Oversold / Target <80% / 63D <25% / Rising Now the stock is in. Shown when Match ≥ 2. Strong is a separate column (not in Match). No retention. Filters are display-only.",
    "desc_multi_signal_zh":
        "仅聚合：Match Count = 同时属于「超卖回调 / Target <80% / 63日<25% / 正在上涨」的数量；Match ≥ 2 入选。Strong 单独列（不计入 Match）。无留存。上方筛选仅改显示。",
    "Match":
        "匹配",
    "Oversold":
        "超卖",
    "Target <80":
        "Target <80",
    "63D <25":
        "63D <25",
    "Strong":
        "Strong",
    "Match Count across Oversold / Target / 63D Low / Rising Now":
        "Match Count = 超卖 / Target / 63日低位 / 正在上涨 命中数",
    "Currently on Strong Watchlist (not in Match Count)":
        "当前在 Strong Watchlist（不计入 Match）",
    "All ≥2":
        "全部 ≥2",
    "3+ Signals":
        "3+ 信号",
    "4 Signals":
        "4 信号",
    "Low + Rising":
        "低位 + 上涨",
    "Oversold + Rising":
        "超卖 + 上涨",
    "Target + Rising":
        "Target + 上涨",
    "Strong + Rising":
        "Strong + 上涨",
    "Up Days 5D":
        "近5日上涨",
    "5D Return":
        "5日涨幅",
    "63D Position":
        "63日位置",
    "Up days in the latest 5 trading days (Close > prior close)":
        "近5个交易日中收盘价高于前一交易日的天数",
    "(Current close / close 5 trading days ago − 1) × 100":
        "（最新收盘价 ÷ 5个交易日前收盘价 − 1）× 100",
    "63D Position < 25%":
        "63日位置 < 25%",
    "Fundamentals / news / Est / MOS / CLV / AI blank on this tab.":
        "本页财报 / 新闻 / Est / MOS / CLV / AI 留空。",
    "Total":
        "共",
    "desc_mine_owner":
        "Long-term My Watchlist (current: {list}). Auto: 🟡 WATCH = 5% below SMA · 🟢 DEEP = 10% below SMA. Manual overrides Auto until reset. Signed in: edit list & alerts; Est.Value / MOS / CLV visible.",
    "desc_mine_owner_zh":
        "长期观察 / 我的自选（当前：{list}）。Auto：🟡 WATCH = SMA 下方 5% · 🟢 DEEP = SMA 下方 10%。Manual 覆盖 Auto 直至重置。已登录：可增删自选、改提醒，并显示 Est.Value / MOS / CLV。",
    "desc_mine_public":
        "Long-term My Watchlist (current: {list}). Auto 🟡/🟢 SMA alerts; Manual overrides until reset. Public page hides Est.Value / MOS / CLV; sign in to edit list & Manual Alert.",
    "desc_mine_public_zh":
        "长期观察 / 我的自选（当前：{list}）。显示 Auto 黄/绿 SMA 提醒；Manual 覆盖直至重置。公开页不显示 Est.Value / MOS / CLV；登录后可改自选与人工提醒价。",
    "desc_temp":
        "Temporary tickers for this browser session only; cleared when the browser closes.",
    "desc_temp_zh":
        "朋友临时输入的股票，仅本次会话有效，关闭浏览器后自动清空。",
    "All groups show pool membership; click column headers to sort. Fundamentals/news may take a few seconds on first open (15‑min cache). Weekly universe + weekday EOD prices, or use the refresh button.":
        "所有组都显示「所属股池」，点表头可排序。财报/新闻首次打开可能等待几秒（缓存 15 分钟）。默认每周更新股池、交易日收盘后更新行情；也可点上方按钮手动刷新。",
    '(Price−Low)/(High−Low)×100; observational only':
        '(现价−Low)/(High−Low)×100；仅观察',
    '20-day average volume':
        '20日平均成交量',
    '20-day average volume (liquidity)':
        '20日平均成交量（流动性）',
    'AI Score V1 = Opportunity (0–100) − Risk penalty':
        'AI Score V1 = 机会分(0–100) − 风险扣分',
    'AI Score V1 = opportunity − risk. Hover for breakdown. Independent of Est.Value/MOS.':
        'AI Score V1 = 机会分 − 风险扣分。悬停看拆分。与 Est.Value/MOS 独立。',
    'AI Score V1 = opportunity − risk. Uses public MOS T; excludes Admin Est.Value / real MOS and 63D Position.':
        'AI Score V1 = 机会分 − 风险扣分。使用公开 MOS T；不含管理员 Est.Value / 真实 MOS，也不含 63D Position。',
    'Alert Price (read-only). Sign in to edit.':
        '人工关注价（只读）。登录后可修改。',
    'Alert Price — saved manually; not auto-updated with price/SMA/target. Click cell to edit.':
        '人工关注价（Alert Price）。持久保存，不随现价/SMA/目标价自动改变。可在单元格内直接修改。',
    'Analyst / rating':
        '分析师 / 评级',
    'Avg absolute daily move over 63 trading days':
        '近63个交易日日均绝对涨跌幅',
    'Business / product':
        '业务 / 产品',
    'CLV applies conservative recovery haircuts to balance-sheet assets, then subtracts all liabilities for a per-share asset floor.':
        'CLV 基于最新资产负债表，对现有资产采用统一的保守回收折扣，再扣除全部负债后计算每股资产底线。',
    'CLV is an asset floor — not a target price or full going-concern value.':
        'CLV 是资产底线参考，不是目标价，也不是对公司持续经营价值的完整估计。',
    'Cache':
        '缓存',
    'CapEx direction':
        '资本支出方向',
    'Cash vs debt':
        '现金 vs 债务',
    'Cash-flow direction (YoY ↑/↓/→ — explanatory only)':
        '现金流方向（同比 ↑/↓/→，仅解释不计红旗）',
    'Company / management':
        '公司 / 管理层',
    'Current ratio':
        '流动比率',
    'Current:':
        '当前：',
    'DCF scenarios; financials/loss/thin data → —':
        'DCF 情景；金融/亏损/数据不足 → —',
    'Day change (last two closes)':
        '当日涨跌幅（最近两个收盘价）',
    'Debt / equity':
        '负债/权益',
    'Distance from short-term SMA':
        '相对短期均值的偏离',
    'EPS growth YoY':
        '盈利增长 YoY',
    'Earnings / guidance':
        '财报 / 指引',
    'Earnings Night Review — date for evening checks':
        '财报夜市审核 — 仅日期，便于晚间核对',
    'FCF↓ CAPEX↑ OCF↑ = investing more (not always bad); FCF↓ OCF↓ = ops stress (OCF flags); FCF↑ = healthy. CapEx alone is not a red flag.':
        'FCF↓ CAPEX↑ OCF↑ = 扩张投资致自由现金流下降，不一定坏；FCF↓ OCF↓ = 经营恶化（OCF 记红旗）；FCF↑ = 健康。CapEx 本身不算红旗。',
    'Financial / legal risk':
        '财务 / 法律风险',
    'Financial quality':
        '财务质量',
    'Free CF direction':
        '自由现金流方向',
    'Fundamental red flags (⚠ counts toward health)':
        '财报红旗（⚠ = 红旗，计入健康分）',
    'Fundamentals health: 7 red-flag checks':
        '财报健康：7 项基本面红旗',
    'Growth anchored on Revenue CAGR (Growth v1.1 frozen)':
        '增长：Revenue CAGR 为锚（Growth v1.1 冻结）',
    'Highest close in last 63 trading days':
        '近63个交易日最高收盘价',
    'Hover Est.Value / CLV for full breakdown':
        '悬停 Est.Value / CLV 可看完整拆分',
    'Index pools: S&P500 / Nasdaq100 / …; MANUAL if not in a pool':
        '指数池：S&P500 / Nasdaq100 / …；不在池内显示 MANUAL',
    'Industry (from universe)':
        '行业（来自股票池）',
    'Investment value (DCF v1.3 frozen + CLV, independent of AI)':
        '投资价值（DCF v1.3 frozen + CLV，与 AI 独立）',
    'Long-term trend':
        '长期趋势',
    'Long-term trend: SMA63 vs SMA252 + SMA252 slope':
        '长期趋势：SMA63 vs SMA252 + SMA252 斜率',
    'Lowest close in last 63 trading days':
        '近63个交易日最低收盘价',
    'NO NEWS = none material in 30d; NEUTRAL = news but muted impact. Major negatives red, positives green. Hover for titles.':
        'NO NEWS = 近30天无重要新闻；NEUTRAL = 有新闻但影响中性。重大负面标红、正面标绿。悬停看标题。',
    'News (last 30 days)':
        '新闻（近30天）',
    'News (last 30 days; sentiment + / −)':
        '新闻（近30天；情绪 + 正 / − 负）',
    'No cached data for “{group}”. Refresh universe weekly, then refresh all prices + Watchlist.':
        '「{group}」还没有缓存数据。请先点「更新股票池成分（每周）」，再点「刷新全部行情 + Watchlist」。',
    'One full table — desktop for analysis, swipe on mobile. Default sort: Dist. from mean % ascending; click headers to re-sort. Trend = SMA63 vs SMA252 + slope. 63D Low/High/Position% observational. 1Y Target / Target Ratio from Yahoo. Earnings date for evening review. RVOL = today vol / 20D avg. Refresh prices to populate new 63D / target fields.':
        '同一张完整表：电脑适合分析，手机可左右滑动。默认按「离均值%」从低到高，点表头可排序。趋势 = SMA63 vs SMA252 + 斜率。63D Low/High/Position% 仅观察。1Y Target / Target Ratio 来自 Yahoo。财报日用于晚间审核。RVOL = 今日量/20日均量。刷新行情后才会写入新的 63D / 目标价字段。',
    'Operating CF direction':
        '经营现金流方向',
    'Operating cash flow':
        '经营现金流',
    'Opp':
        '机会',
    'Other':
        '其他',
    'Price ÷ 1Y Target (lower = more interesting; sort via header). Independent of DCF/CLV.':
        '现价 ÷ 1Y Target（越小越值得关注；点表头排序）。与 DCF/CLV 独立。',
    'Public visitors can browse My Watchlist quotes but cannot edit the list; Est / MOS / CLV require':
        '公开访客可浏览我的自选行情，但不能修改名单；Est / MOS / CLV 需',
    'Pullback depth':
        '回调深度',
    'RVOL = today volume / 20D avg volume':
        'RVOL = 今日量 / 前20日均量',
    'RVOL = today volume / 20D avg; not always higher=better':
        'RVOL = 今日量 / 前20日均量。结合当日%解读，不是越大越好',
    'Rebound confirm':
        '反弹确认',
    'Refresh all index-pool prices and sync Watchlist (incl. MANUAL)':
        '刷新全部指数股池行情缓存，并同步更新 Watchlist（含 MANUAL）',
    'Revenue growth YoY':
        '营收增长 YoY',
    'Risk':
        '风险',
    'Risk penalties: severe financial −0~15, major negative news −0~15, volume dump −0~5, high vol/low liquidity −0~5, near earnings −0~15. Hover AI for breakdown. Green≥70 / yellow 40–69 / red <40. Excludes Est.Value / MOS / 63D Position.':
        '风险扣分：严重财务 −0~15、重大负面新闻 −0~15、放量暴跌 −0~5、高波动/低流动 −0~5、临近财报 −0~15。悬停 AI 看拆分。绿≥70 / 黄40–69 / 红<40。不含 Est.Value / MOS / 63D Position。',
    'Risk penalties: severe financial −0~15, major negative news −0~15, volume dump −0~5, high vol/low liquidity −0~5, near earnings −0~15. Hover AI for breakdown. Green≥70 / yellow 40–69 / red <40. 63D Position and Admin-only Intrinsic Value / real MOS are excluded from AI Score V1.':
        '风险扣分：严重财务 −0~15、重大负面新闻 −0~15、放量暴跌 −0~5、高波动/低流动 −0~5、临近财报 −0~15。悬停 AI 看拆分。绿≥70 / 黄40–69 / 红<40。63D Position 与管理员内在价值 / 真实 MOS 不计入 AI Score V1。',
    'SMA / schedule':
        '改均线 / 定时',
    'Show: 🟢 good (0) / 🟡 fair (1–2) / 🔴 weak (≥3) + pass/known, then flag codes (e.g. REV− DEBT−). Hover for values.':
        '显示：🟢好(0旗) / 🟡一般(1–2) / 🔴差(≥3) + 通过/已知，后面列红旗代码。悬停看数值。',
    'Target Ratio = Price / 1Y Target; lower = more interesting':
        'Target Ratio = 现价 / 1Y Target；越小越值得关注',
    'Uniform recovery: Cash 100% | Marketable Securities 100% | Receivables 80% | Inventory 50% | Non-marketable Investments 50% | PP&E 25% | Goodwill & Intangibles 0%. Same rules for all names; not tuned to market price.':
        '统一 recovery：Cash 100% | Marketable Securities 100% | Receivables 80% | Inventory 50% | Non-marketable Investments 50% | PP&E 25% | Goodwill & Intangibles 0%。所有公司相同规则，不根据市场价格调整。',
    'Universe':
        '股票池',
    'Updated':
        '更新',
    'Updating…':
        '更新中…',
    'Value reference:':
        '价值参考：',
    'Volume':
        '成交量',
    'Yahoo 1Y analyst mean target':
        'Yahoo 分析师一年目标均价',
    'cash < debt':
        '现金<债务',
    'more investment':
        '投资增加',
    'negative/worsening':
        '负数或恶化',
    'row price':
        '行内最新现价',
    'stale price (default >72h) → —; DCF not re-run':
        '价格过期（默认 >72h）→ —，不重跑 DCF',
    'vs DCF: CLV = conservative asset floor; DCF = going-concern cash flows. If DCF Base < CLV, show a warning — DCF is not auto-changed.':
        '与 DCF 区别：CLV = 已有资产的保守价值底线；DCF = 持续经营下未来现金流估计。当 DCF Base < CLV 时显示 warning，但不自动修改 DCF。',
    'Universe updated: S&P500 {sp500} + Nasdaq100 {ndx100} + S&P400 {sp400} + S&P600 {sp600} + TSX {tsx} → {unique} unique':
        '股票池已更新：S&P500 {sp500} + Nasdaq100 {ndx100} + S&P400 {sp400} + S&P600 {sp600} + TSX {tsx} → 去重后 {unique} 只',
    'Universe update failed: {exc}':
        '更新股票池失败：{exc}',
    'Prices refreshed ({group}): ok {ok} / errors {errors} (SMA{sma}, universe {universe})':
        '行情已刷新（{group}）：成功 {ok} / 失败 {errors}（SMA{sma}，本组 {universe} 只）',
    'Price refresh failed: {exc}':
        '刷新行情失败：{exc}',
    'All pools refreshed: ok {ok} / errors {errors} (universe {universe}) · Watchlist ok {watchlist_ok} / errors {watchlist_errors}':
        '全部股池行情已刷新：成功 {ok} / 失败 {errors}（共 {universe} 只）· Watchlist 成功 {watchlist_ok} / 失败 {watchlist_errors}',
    'All pools refreshed: ok {ok} / errors {errors} (universe {universe}) · '
    'Watchlist ok {watchlist_ok} / errors {watchlist_errors} · '
    'Research Strong {strong} · Rising {rising}':
        '全部股池行情已刷新：成功 {ok} / 失败 {errors}（共 {universe} 只）· Watchlist 成功 {watchlist_ok} / 失败 {watchlist_errors} · '
        'Research Strong {strong} · Rising {rising}',
    'All pools / Watchlist refresh failed: {exc}':
        '全部股池 / Watchlist 刷新失败：{exc}',
    'Saved: SMA={sma}, rebound lookback={rebound}. Auto: universe weekly {weekday} {uh:02d}:{um:02d} PT; prices weekdays {ph:02d}:{pm:02d} PT after US close. Restart app for in-app schedule; Windows tasks use install-time values.':
        '已保存：SMA={sma}，反弹回看={rebound}。自动更新：公司名每周{weekday} {uh:02d}:{um:02d}（太平洋时间）；列表行情工作日 {ph:02d}:{pm:02d}（太平洋时间，美股收盘后）。重启应用后日程生效；Windows 计划任务也会按安装时的时间运行。',
    'Save failed: {exc}':
        '保存失败：{exc}',
    'Invalid {label}':
        '{label}无效',
    'A unified stock research platform: single-name analysis + market screening + configurable SMA + earnings night review.':
        '统一股票决策平台：单股分析 + 大盘筛选 + 可配置均线 + 财报夜市审核。',
    'AI-assisted stock research, systematic screening, valuation reference, and paper-trading experiments.':
        'AI辅助股票研究、系统化筛选、估值参考与模拟交易实验平台',
    'AI-assisted stock research, systematic screening, valuation references, and paper-trading experiments.':
        'AI辅助股票研究、系统化筛选、估值参考与模拟交易实验平台',
    'Research Universe':
        '研究股票池',
    'Open →':
        '打开 →',
    "Today's LeiBot":
        '今日 LeiBot',
    'AI Candidates':
        'AI 候选',
    'Top':
        '前',
    'Paper Equity':
        '模拟权益',
    'Single-stock research, charts and financial analysis.':
        '个股研究、图表与财务分析。',
    'Systematic market screening across supported stock universes.':
        '在支持的股票池中进行系统化市场筛选。',
    'AI candidates, oversold stocks, Target Ratio, 63D Position and personal watchlists.':
        'AI 候选、超卖标的、Target Ratio、63D Position 及个人观察列表。',
    'Public AI Paper Trading experiment with positions, P&L and performance history.':
        '公开 AI 模拟交易实验：持仓、盈亏与绩效历史。',
    'AI Stock Ranking':
        'AI 股票排名',
    'Systematic opportunity scoring using price behavior, trend, financial quality, news and MOS T.':
        '结合价格表现、趋势、财务质量、新闻和 MOS T 的系统化机会评分。',
    'Market Screening':
        '市场筛选',
    'Screen supported large-cap, mid-cap, small-cap and Canadian stock universes.':
        '筛选支持的大盘、中盘、小盘及加拿大股票池。',
    'Track oversold opportunities, Target Ratio, 63D Position and personal selections.':
        '跟踪超卖机会、Target Ratio、63D Position 及个人选股。',
    'Target Valuation':
        '目标估值参考',
    'Use 1Y Target, Target Ratio and MOS T as transparent valuation reference indicators.':
        '使用 1Y Target、Target Ratio 和 MOS T 作为透明的估值参考指标。',
    'Test highly ranked stocks with systematic allocation, Stop Loss and Take Profit rules.':
        '使用系统化资金分配、止损和止盈规则测试高排名股票。',
    'Performance History':
        '历史绩效',
    'Track positions, realized/unrealized P&L, win rate and long-term strategy performance.':
        '跟踪持仓、已实现/未实现盈亏、胜率及长期策略表现。',
    'Implementation notes for developers and advanced users. The public AI Trading page is an internal Paper Trading simulator and does not submit brokerage orders.':
        '面向开发者与高级用户的实现说明。公开 AI 交易页是内部模拟交易系统，不会向券商提交订单。',
    'Configurable SMA period and rebound lookback in Settings.':
        '可在设置中配置 SMA 周期与反弹回看天数。',
    'Dashboard ranking uses Dist. from mean % = (Price − SMA) / SMA.':
        '看板排序使用离均值% =（现价 − SMA）/ SMA。',
    'Rebound % from a recent lookback low; earnings date for evening review.':
        '反弹率基于近期回看低点；财报日用于夜间复盘。',
    'Shared SQLite database (leibot.db) across Stock Tracker, Market Dashboard, Watchlist and Paper Trading.':
        'Stock Tracker、Market Dashboard、Watchlist 与模拟交易共用 SQLite 数据库（leibot.db）。',
    'Data provider currently uses Yahoo Finance; IBKR is a future upgrade path for a separate Admin trading system — not connected to public Paper Trading.':
        '当前数据源为 Yahoo Finance；IBKR 是面向独立管理端交易系统的未来升级路径 — 未连接公开模拟交易。',
    'Key Features':
        '核心功能',
    'Architecture':
        '架构',
    'Single-name research with charts and fundamental snapshots for USD & CAD tickers.':
        '查询美股 / 加股，生成走势图，并展示市值、PE、EPS、股息、利润率等决策指标。',
    'Deduplicated large-cap universe ranked by distance from moving average.':
        '去重大盘股池，按「离均线%」排序，适合找相对弱势或超跌标的。',
    'Configurable SMA window — not hard-coded.':
        '平均周期可配置：预设或手动修改，刷新即可重算。',
    '(Price − SMA) / SMA — primary ranking signal for the dashboard.':
        '现价相对均线的偏离幅度，默认从低到高排序。',
    'Rebound from the recent lookback low — helps spot bounce vs still falling.':
        '相对近期低点的反弹幅度，辅助判断是否已经止跌。',
    'Earnings date only — designed for evening news review before overnight risk.':
        '财报列只显示日期。盘后 / 晚上对照新闻与预期，再决定是否买卖。',
    'One shared database for the whole LeiBot platform.':
        'Stock Tracker 与 Dashboard 共用一个 SQLite 数据库。',
    'Provider layer ready for IBKR upgrade without rewriting the UI.':
        '当前 Yahoo Finance；架构预留 Interactive Brokers API。',
    'Volume confirm':
        '↑确认',
    'Volume dump · check news':
        '放量跌·查新闻',
    'RVOL guide: <0.7 light; 0.7–1.2 normal; 1.2–2 confirm; 2–3 heavy (with news); >3 unusual — don’t score mechanically.':
        'RVOL：<0.7 缺量；0.7–1.2 正常；1.2–2 放量确认；2–3 明显放量(结合新闻)；>3 异常，勿机械加分。',
    'Remove from My Watchlist':
        '从我的自选移除',
    'Needs a valid Est.Value and a fresh Watchlist price':
        '需要有效的 Est.Value 与新鲜 Watchlist 现价',
    'Alert Price. Enter/blur to save; clear then save to delete. Does not trigger trades.':
        '人工关注价。回车或失焦保存；清空后保存即删除。不触发自动交易。',
    'READY — price entered alert zone (review fundamentals/news; not an auto BUY)':
        'READY — 现价已进入关注区（可复查财报/新闻/买入条件，不代表自动 BUY）',
    'NEAR — within 5% of alert':
        'NEAR — 距关注价 ≤5%',
    'SMA period must be between 5 and 250':
        '平均周期需在 5–250 之间',
    'Rebound lookback must be between 5 and 250':
        '反弹回看天数需在 5–250 之间',
    'Invalid universe weekday':
        '公司名更新星期无效',
    'Universe update time':
        '公司名更新时间',
    'Price update time':
        '收盘行情时间',
    'Risk Disclaimer':
        '风险提示',
    'Stock markets are unpredictable and involve risk. This project is for educational, research, and experimental purposes only and does not constitute investment advice. Users are encouraged to use Paper Trading or small experimental positions through Fractional Shares and are solely responsible for their own investment decisions and risks.':
        '股市风险莫测。本项目仅供教学、研究与实验使用，不构成投资建议。建议使用 Paper Trading（模拟交易）或以 Fractional Shares（碎股）进行小额实验；用户须自行承担投资决策与风险。',
    'Stock markets are inherently unpredictable and involve risk. This project is intended solely for educational, research, and experimental purposes. Users are encouraged to use <strong>Paper Trading</strong> or approximately <strong>CAD/USD 100</strong> in small experimental capital through <strong>Fractional Shares</strong>. Any data, analysis, valuation, scoring, or other information provided by this project <strong>does not constitute investment advice</strong>. Users are solely responsible for their own investment decisions and risks.':
        '股市存在不确定性且伴随风险。本项目仅供教学、研究与实验使用，不构成投资建议。建议使用 Paper Trading（模拟交易）或以 Fractional Shares（碎股）进行小额实验；用户须自行承担投资决策与风险。',
    # Dashboard group tabs + remaining column labels
    'S&P500 + Nasdaq100':
        'S&P500 + 纳斯达克100',
    'Mid Cap · S&P 400':
        '中盘 · S&P 400',
    'Small Cap · S&P 600':
        '小盘 · S&P 600',
    'Canada · S&P/TSX Composite':
        '加拿大 · S&P/TSX 综指',
    '63D Low':
        '63日低',
    '63D High':
        '63日高',
    '63D Position%':
        '63日位置%',
    '1Y Target':
        '一年目标价',
    'Target Ratio':
        '目标价比率',
    'Yahoo 1-year analyst mean target price':
        'Yahoo 分析师一年目标均价',
    'MOS T':
        'MOS T',
    'MOS T — Margin of Safety based on 80% of the 1-Year Analyst Target Price.':
        'MOS T — 基于一年分析师目标价 80% 的安全边际（临时目标价代理，不是内在价值 MOS）。',
    'temporary target-based MOS using Base T = 1Y Target × 80% (not intrinsic MOS).':
        '临时目标价代理：Base T = 一年目标价 × 80%（不是内在价值 MOS）。',
    'Code Guide':
        '代码说明',
    'Details':
        '详情',
    'Technical Details':
        '技术详情',
    'Data ready':
        '数据已就绪',
    'News: Financial ≥60%':
        '新闻：财报通过率 ≥60%',
    'stocks':
        '只股票',
    'Guide':
        '说明',
    'SKIPPED':
        '已跳过',
    'News skipped — Financial Score < 60%':
        '已跳过新闻 — 财报得分 < 60%',
    'News skipped — Financial Score &lt; 60%. Gate only; not a buy filter. SKIPPED ≠ NEUTRAL.':
        '已跳过新闻 — 财报得分 < 60%。仅为新闻分析门槛，不是买入条件。SKIPPED ≠ NEUTRAL。',
    'Analyzed: no material news in 30d (NEUTRAL, score 0). Different from SKIPPED.':
        '已分析：近30天无实质新闻（NEUTRAL，得分 0）。与 SKIPPED 不同。',
    'News: Financial ≥60% → analyze (cache if fresh). Below → SKIPPED (score 0, no API). SKIPPED ≠ NEUTRAL. News Score ±5 in AI only — not a buy filter.':
        '新闻：财报≥60% 才分析（缓存未过期则复用）；否则 SKIPPED（得分0，不调 API）。SKIPPED ≠ NEUTRAL。新闻仅以 ±5 计入 AI，不是买入条件。',
    'Pipeline: Financial first; News only if Financial ≥60%. SKIPPED = never analyzed (score 0). NEUTRAL / NO NEWS = analyzed, no material signal (score 0). POSITIVE +5 / NEGATIVE −5. Cache reused when fresh. Hover for titles.':
        '流水线：先算财报；仅当财报≥60% 才分析新闻。SKIPPED=未分析（0分）。NEUTRAL/NO NEWS=已分析但无实质信号（0分）。POSITIVE +5 / NEGATIVE −5。新鲜缓存可复用。悬停看标题。',
    'Risk penalties: severe financial −0~15, volume dump −0~5, high vol/low liquidity −0~5, near earnings −0~15. News Score is independent (±5: +5 / 0 / −5 / SKIPPED 0). Hover AI for breakdown. Green≥70 / yellow 40–69 / red <40. 63D Position and Admin-only Intrinsic Value / real MOS are excluded from AI Score V1.':
        '风险扣分：严重财务 −0~15、放量下跌 −0~5、高波动/低流动性 −0~5、临近财报 −0~15。新闻为独立分量（±5：+5 / 0 / −5 / SKIPPED 0）。悬停 AI 看明细。绿≥70 / 黄40–69 / 红<40。63日位置与管理员估值/真实 MOS 不计入 AI Score V1。',
    'Updated:':
        '更新：',
    'Current group':
        '当前分组',
    'Close':
        '关闭',
    'Est.Value / MOS% / CLV visible (signed in).':
        '已登录：Est.Value / MOS% / CLV 可见。',
    # Admin Order Requests / Private Local Agent API
    'Admin only · Private Local Agent API · No IBKR':
        '仅管理员 · 私有本地 Agent API · 不含 IBKR',
    # Research (Strong Stock Monitor)
    '研究中心':
        '研究中心',
    'Research failed to load data. Try Rebuild / Backfill.':
        '研究中心加载失败。请尝试「重建 / 回填」。',
    'Strong Stock Monitor failed to load data. Try Rebuild / Backfill.':
        '研究中心加载失败。请尝试「重建 / 回填」。',
    'Daily Strong Stocks':
        '每日强势股',
    'COUNT20 Ranking':
        '强势次数排行',
    'Strong Watchlist':
        '强势 Watchlist',
    'Only stocks meeting the Strong Day Position threshold on that date. Column lengths differ by day.':
        '仅列出当日达到强势日 Position 阈值的股票；各列家数可以不同。',
    'Historical frequency over the latest 20 trading days. Current Position may be below the Strong Day threshold.':
        '近 20 个交易日的历史出现频率；当前 Position 可以低于强势日阈值。',
    'COUNT ≥ threshold qualifies; retain N trading days after last qualify; renew resets retention. Position may be below the Strong Day threshold during retention (pullback watch).':
        'COUNT 达到阈值即入选；自最近达标日起保留 N 个交易日；再次达标则续期重置。保留期内 Position 可低于强势日阈值（回调观察）。',
    'COUNT ≥ threshold qualifies; retain 20 trading days; renew on later qualify. Position may be below the Strong Day threshold during retention (pullback watch).':
        'COUNT 达到阈值即入选；保留交易日数见规则条；再次达标则续期。保留期内 Position 可低于强势日阈值（回调观察）。',
    'Each date lists only Strong Day stocks (Position ≥ threshold), sorted by Position %. Header shows (n) stocks.':
        '每个日期只列出强势日股票（Position ≥ 阈值），按 Position% 降序；表头显示（家数）。',
    'Threshold':
        '阈值',
    'Current Position may be below the Strong Day threshold — COUNT is historical frequency, not today’s Strong-Day list.':
        '当前 Position 可以低于强势日阈值 — COUNT 是历史频率，不是今日强势日名单。',
    'Retention rows may show Position below the Strong Day threshold on purpose.':
        '保留中的行故意可能显示 Position 低于强势日阈值。',
    'Only stocks with 63D Position ≥ 80% on that date. Column lengths differ by day.':
        '仅列出当日达到强势日 Position 阈值的股票；各列家数可以不同。',
    'Financials and News stay blank for now. Adjust COUNT threshold after reviewing the distribution.':
        '财务与新闻暂空。请先查看 COUNT 分布，再调整达标阈值。',
    'Total':
        '合计',
    'No daily strong-stock data yet.':
        '尚无每日强势股数据。',
    'Position':
        'Position',
    'COUNT distribution':
        'COUNT 分布',
    '20 → 0':
        '20 → 0',
    'No COUNT20 ranking rows yet.':
        '尚无 COUNT20 排行数据。',
    'Current 63D Position':
        '当前 63D Position',
    'Last Strong Date':
        '最近强势日',
    'On list':
        '已在名单',
    'Qualifies':
        '可达标',
    'Qualifying':
        '达标',
    'Retention':
        '保留中',
    'As of':
        '截至',
    'Updated':
        '更新',
    'No Strong Watchlist data yet — run historical backfill.':
        '尚无强势股名单 — 请先运行历史回填。',
    'Rebuild Strong Watchlist from ~1y history? This may take several minutes.':
        '用约 1 年历史重建强势股名单？可能需要数分钟。',
    'Rebuild / Backfill':
        '重建 / 回填',
    'No active strong stocks under the current rules.':
        '当前规则下没有活跃强势股。',
    'First Qualified':
        '首次达标',
    'Last Qualified':
        '最近达标',
    'Days Remaining':
        '剩余天数',
    'Financials':
        '财务',
    'News':
        '新闻',
    'Add to My Watchlist':
        '加入我的自选',
    'Please sign in to manage Research':
        '请先登录后再使用研究中心',
    'Please sign in to manage Strong Stock Monitor':
        '请先登录后再使用研究中心',
    'Strong Watchlist rebuilt: {n} active (as of {day})':
        '强势股名单已重建：{n} 只活跃（截至 {day}）',
    'Strong backfill failed: {exc}':
        '强势股回填失败：{exc}',
    'Added {ticker} to My Watchlist':
        '已将 {ticker} 加入我的自选',
    '63D Position':
        '63D Position',
    'Price':
        '现价',
    'Status':
        '状态',
    # Admin Order Requests / Private Local Agent API
    'Admin only · Private Local Agent API · No IBKR':
        '仅管理员 · 私有本地 Agent API · 不含 IBKR',
    'Safety':
        '安全说明',
    'Creating an Order Request only stores an internal PENDING record for your Local Trading Agent. It does not connect to IBKR and does not place any brokerage order.':
        '创建订单请求仅会写入一条内部 PENDING 记录，供本地交易 Agent 读取。不会连接 IBKR，也不会下任何券商订单。',
    'API key not configured':
        '未配置 API 密钥',
    'Set environment variable LEIBOT_PRIVATE_AGENT_API_KEY (min 16 characters) for the Local Agent Bearer token.':
        '请设置环境变量 LEIBOT_PRIVATE_AGENT_API_KEY（至少 16 个字符）作为本地 Agent 的 Bearer 令牌。',
    'Private agent API key is configured via environment variable (not shown here).':
        '私有 Agent API 密钥已通过环境变量配置（此处不显示）。',
    'Create Order Request':
        '创建订单请求',
    'Mode is fixed to PAPER for V0. Status starts as PENDING.':
        'V0 模式下固定为 PAPER，初始状态为 PENDING。',
    'Action':
        '方向',
    'Quantity':
        '数量',
    'Mode':
        '模式',
    'Expected Price':
        '预期价格',
    'Allocation Amount':
        '分配金额',
    'Recent Order Requests':
        '最近订单请求',
    'No order requests yet.':
        '暂无订单请求。',
    'Request ID':
        '请求 ID',
    'Created':
        '创建时间',
    'Allocation':
        '分配',
    'Stop':
        '止损',
    'Status':
        '状态',
    'Please sign in to manage Order Requests':
        '请先登录后再管理订单请求',
    'Order Request #{id} created ({symbol}, PENDING)':
        '已创建订单请求 #{id}（{symbol}，PENDING）',
    # AI Paper Trading
    'AI Trading':
        'AI 交易',
    'AI Paper Trading':
        'AI 模拟交易',
    'Simulation only — no real brokerage orders':
        '仅模拟 — 不会下真实券商订单',
    'Paper Trading notice':
        '模拟交易说明',
    'All trades on this page are simulated. Results do not represent guaranteed real-world execution, fill quality, or slippage. This project is for educational and research purposes and does not constitute investment advice.':
        '本页所有交易均为模拟。结果不代表真实成交、滑点或执行质量。本项目仅供教育与研究，不构成投资建议。',
    'Starting Capital':
        '起始资金',
    'Current Equity':
        '当前权益',
    'Trading Limit':
        '交易额度',
    'Invested':
        '已投入',
    'Cash':
        '现金',
    "Today's P&L":
        '今日盈亏',
    'Total Realized P&L':
        '累计已实现盈亏',
    'Total Unrealized P&L':
        '累计未实现盈亏',
    'Total Return':
        '总回报',
    'Win Rate':
        '胜率',
    'Closed Trades':
        '已平仓笔数',
    'Open Positions':
        '持仓',
    'Today':
        '今日',
    'History':
        '历史',
    'Trade History':
        '交易历史',
    'DOWNLOAD AI TRADING DATA (.XLSX)':
        '下载 AI Trading 数据 (.XLSX)',
    'RESET AI TRADING':
        '重置 AI Trading',
    'Reset AI Trading?':
        '重置 AI Trading？',
    'This will permanently clear the current AI Trading experiment, including:':
        '将永久清空当前 AI Trading 实验，包括：',
    'It will NOT delete:':
        '不会删除：',
    'Download Excel first if you want to keep a copy of this trading experiment.':
        '如需保留本轮实验记录，请先下载 Excel。',
    'Download Excel':
        '下载 Excel',
    'AI Trading reset: trades {t} · priority {p} · cash restored ${c:.2f}. Discovery / Saved News kept.':
        'AI Trading 已重置：交易 {t} · 优先买入 {p} · 现金恢复 ${c:.2f}。Discovery / 已保存新闻保留。',
    'Excel export failed: {exc}':
        'Excel 导出失败：{exc}',
    'Unsaved news auto-clears after 7 full days · ★ Saved News kept until you delete':
        '未保存新闻满 7 个自然日后清除 · ★ 优先新闻满 7 日后可手工删除',
    'Unsaved news auto-clears after 7 full days · ★ PRIORITY Delete only after 7 days':
        '未保存新闻满 7 个自然日后清除 · ★ 优先新闻满 7 日后才可删除',
    'PRIORITY NEWS':
        '优先新闻',
    'PRIORITY':
        '优先',
    'PRIORITY — keep in News History past 7 days until manually deleted':
        '优先 — 满 7 日后仍保留在新闻历史，可手工删除',
    'Non-priority auto-clears after 7 days · ★ PRIORITY kept until you delete':
        '未保存新闻满 7 个自然日后清除 · ★ 优先新闻满 7 日后可手工删除',
    'No PRIORITY news yet. Star ★ PRIORITY on Broad Discover / Official 5×5.':
        '暂无优先新闻。请在 Broad Discover / Official 5×5 最右列点 ★ PRIORITY。',
    'No other stored news in archive.':
        '归档中暂无其他新闻。',
    'Delete this PRIORITY news from History? This cannot be undone.':
        '从新闻历史删除这条优先新闻？此操作不可撤销。',
    'Removed from News History: {ticker}':
        '已从新闻历史移除：{ticker}',
    'News History keeps items for {days} full days — delete is disabled until then ({ticker}, day {age}).':
        '新闻历史需保留满 {days} 个自然日，此前不可删除（{ticker}，第 {age} 天）。',
    'News History keeps items for 7 full days — Delete unlocks after that.':
        '新闻历史需保留满 7 个自然日，之后才可删除。',
    'Keep 7d':
        '满7日可删',
    'Delete':
        '删除',
    'News Priority':
        '新闻优先',
    'News Priority {state}: {ticker}':
        '新闻优先 {state}：{ticker}',
    'on':
        '开',
    'off':
        '关',
    'Archive of stored discovery events (recent + older). Star ⭐ pins News Priority for long-term visibility — not Priority Buy, and never auto-trades.':
        '已存储的发现事件档案（近期 + 更早）。星标 ⭐ 为新闻优先（长期置顶）— 不是优先买入，也不会自动下单。',
    'No stored news events yet. Run Harvest from AI Discovery first.':
        '暂无已存储新闻事件。请先在 AI Discovery 运行采集。',
    'Recent':
        '近期',
    'Archive':
        '归档',
    'Stop Loss':
        '止损',
    'Take Profit':
        '止盈',
    'Stop Loss %':
        '止损 %',
    'Take Profit %':
        '止盈 %',
    'Refresh AI Candidates':
        '刷新 AI 候选',
    'Create Paper Orders':
        '创建模拟订单',
    'Run daily paper update':
        '运行每日模拟更新',
    'Public view — sign in to create paper orders, mark Priority, or run updates.':
        '公开浏览 — 登录后可创建模拟订单、标记优先或运行更新。',
    'AI Candidates — Top 10':
        'AI 候选 — Top 10',
    'Highest AI Score from Oversold screening. Priority ⭐ is a separate human flag and does not change AI Score. Candidates do not become positions until Create Paper Orders.':
        '来自超卖筛选的最高 AI Score。优先 ⭐ 是人工标记，不会改变 AI Score。候选不会自动开仓，需点击“创建模拟订单”。',
    'Highest AI Score from combined system screening: Oversold + Target Ratio < 80% + 63D Position < 25%, deduplicated. Priority ⭐ is a separate human flag and does not change AI Score. Candidates do not become positions until Create Paper Orders. My Watchlist and Temp are not included automatically.':
        '来自系统筛选组合（超卖回调 + Target Ratio < 80% + 63日位置 < 25%）的最高 AI Score 股票，已去重。优先 ⭐ 是人工标记，不会改变 AI Score。候选不会自动开仓，需点击“创建模拟订单”。我的自选与临时列表不会自动纳入。',
    'Source':
        '来源',
    'Source at Entry':
        '开仓来源',
    'Oversold':
        '超卖',
    'Target':
        '目标价',
    '63D':
        '63日',
    'Manual Priority':
        '人工优先',
    'Add Priority tickers, e.g. AMD, SHOP.TO':
        '添加优先标的，例如 AMD, SHOP.TO',
    'Mark Priority ⭐':
        '标记优先 ⭐',
    'Priority list:':
        '优先列表：',
    'Clear Priority':
        '清除优先',
    'No candidates yet. Refresh AI Candidates after prices are available.':
        '暂无候选。价格就绪后请刷新 AI 候选。',
    'Symbol':
        '代码',
    'Company':
        '公司',
    'AI Score':
        'AI 评分',
    'Priority':
        '优先',
    'Priority Buy':
        '优先买入',
    'Add Priority Buy tickers, e.g. AMD, SHOP.TO':
        '添加优先买入代码，例如 AMD, SHOP.TO',
    'Mark Priority Buy ⭐':
        '标记优先买入 ⭐',
    'Priority Buy list:':
        '优先买入列表：',
    'Clear Priority Buy':
        '清除优先买入',
    'Priority Buy ⭐ — Admin flag: earlier suggested allocation on Create Paper Orders. Does not change AI Score.':
        '优先买入 ⭐ — Admin 标记：创建纸上订单时更早获得建议仓位。不改变 AI Score。',
    'Public view — sign in to create paper orders, mark Priority Buy, or run updates.':
        '公开只读 — 登录后可创建纸上订单、标记优先买入或运行更新。',
    'Strict paper-trading list: Oversold + Target Ratio < 80% + 63D Position < 25% (low position / quality screens), ranked by existing AI Score. Rising Now / 5D metrics are timing references only and do not change AI Score. Priority Buy ⭐ moves a name earlier in suggested allocation (does not change AI Score). Orders are not created until you click Create Paper Orders. Research more names on Candidate Analysis → My Watchlist.':
        '严格纸上交易列表：超卖 + Target Ratio <80% + 63日位置 <25%（低位/质量筛），按现有 AI Score 排序。正在上涨 / 5日指标仅为时机参考，不改 AI Score。优先买入 ⭐ 会让该标的更早获得建议仓位（不改 AI Score）。点击「创建纸上订单」才会建仓。更多研究：候选分析 → 我的自选。',
    'Add names to My Watchlist for research. Prefer Priority Buy on AI Trading when you want earlier allocation — nothing auto-selects for AI Trading.':
        '研究请加入「我的自选」。若要优先分配仓位，请在 AI Trading 标记「优先买入」——不会自动进入 AI Trading。',
    'Deduplicated research universe combining valuation, position, momentum, strength and financial signals. Manual My Watchlist only — nothing auto-selects for AI Trading. Use Priority Buy on AI Trading for earlier allocation.':
        '去重研究宇宙：汇总估值、仓位、动量、强势与财报信号。仅手动「我的自选」——不会自动进入 AI Trading。需要更早分配仓位时，请在 AI Trading 使用「优先买入」。',
    'Please confirm':
        '请确认',
    'Confirm':
        '确认',
    'Confirm close':
        '确认平仓',
    'Create orders':
        '创建订单',
    'Re-buy now':
        '立即重新持仓',
    'Re-enter now':
        '立即重新入场',
    'Close this paper position at the latest market price? This cannot be undone.':
        '将按最新市价平仓该模拟持仓？此操作不可撤销。',
    'Buy $':
        '买入 $',
    'Buy sh':
        '买入股数',
    'Buy':
        '买入',
    'Buy amount in $':
        '买入金额（美元）',
    'Buy shares (optional)':
        '买入股数（可选）',
    'Manual buy / add':
        '手动买入 / 加仓',
    'Enter Buy $ or Buy shares first.':
        '请先填写买入金额或股数。',
    'Buy / add at the latest list price? Cash and trading limit still apply.':
        '按列表最新价买入/加仓？仍受现金与交易额度限制。',
    'Manual buy $ — enter dollars to buy or add. Cash and trading limit still apply.':
        '手动买入 $ — 填写金额以开仓或加仓。仍受现金与交易额度限制。',
    'Manual buy shares — optional; overrides Buy $ if both set.':
        '手动买入股数 — 可选；若同时填写，以股数为准。',
    'End columns Buy $ / Buy sh / Buy: enter dollars or shares, then Buy. Opens a new position or adds to an existing one. Cash and trading limit still apply; blocked names show a warning.':
        '末尾「买入 $ / 股数 / 买入」：填写金额或股数后点买入。可新建仓或对已有持仓加仓。现金不足或超额度会警告拦截。',
    'Added to position: {ticker} +{shares} sh @ {price} · cost +{cost}':
        '已加仓：{ticker} +{shares} 股 @ {price} · 成本 +{cost}',
    'Manual buy opened: {ticker} · {shares} sh @ {price} · cost {cost}':
        '已手动开仓：{ticker} · {shares} 股 @ {price} · 成本 {cost}',
    'Suggested Allocation':
        '建议仓位',
    'Shares':
        '股数',
    'No open paper positions.':
        '暂无模拟持仓。',
    'No closed paper trades yet.':
        '暂无已平仓模拟交易。',
    'Entry Date':
        '开仓日期',
    'Exit Date':
        '平仓日期',
    'Entry Price':
        '开仓价',
    'Exit Price':
        '平仓价',
    'Current Price':
        '现价',
    'Cost':
        '成本',
    'Market Value':
        '市值',
    'Stop':
        '止损价',
    'Invested Amount':
        '投入金额',
    'Realized P&L':
        '已实现盈亏',
    'Return %':
        '收益率 %',
    'Exit Reason':
        '平仓原因',
    'AI Score at Entry':
        '开仓 AI 评分',
    'MOS T at Entry':
        '开仓 MOS T',
    'Current AI Score':
        '当前 AI 评分',
    'Position Status':
        '持仓状态',
    'Open':
        '持仓中',
    'Manual Exit':
        '手动平仓',
    'Unrealized P&L':
        '未实现盈亏',
    'Unrealized P&L %':
        '未实现盈亏 %',
    'Create paper orders from suggested allocations? This is simulated only.':
        '按建议仓位创建模拟订单？仅模拟，不会下真实单。',
    'Manually exit this paper position?':
        '手动平仓该模拟持仓？',
    'Please sign in to manage Paper Trading':
        '请登录后管理模拟交易',
    'Admin: click Stop/Take to override; Reset restores AUTO. Manual does not bypass Knife Risk.':
        'Admin：点击 Stop/Take 可覆盖；重置恢复 AUTO。手动价不会绕过 Knife Risk。',
    'Stop/Take shown read-only — Admin can override before Create Paper Orders.':
        'Stop/Take 为只读展示 — Admin 可在创建纸上订单前覆盖。',
    'Stop Loss — AUTO from Settings %; Admin can override. Reset restores AUTO.':
        '止损 — 默认来自设置百分比；仅 Admin 可覆盖。重置恢复 AUTO。',
    'Take Profit — AUTO from Settings %; Admin can override. Reset restores AUTO.':
        '止盈 — 默认来自设置百分比；仅 Admin 可覆盖。重置恢复 AUTO。',
    'Stop Risk % · Reward % · Reward/Risk':
        '止损风险% · 收益% · 盈亏比',
    'R/R = Reward% ÷ Risk% (Take Profit % / Stop Loss %). Example: +10% / 5% = 2.0. Higher usually means more reward per unit of risk.':
        'R/R = 收益% ÷ 风险%（止盈% / 止损%）。例如 +10% / 5% = 2.0。数值越高，通常单位风险对应的潜在收益越大。',
    'R/R = Reward% ÷ Risk%':
        'R/R = 收益% ÷ 风险%',
    'Manual Stop — Admin override. Reset restores AUTO.':
        '手动止损 — Admin 覆盖。重置恢复 AUTO。',
    'Manual Take Profit — Admin override. Reset restores AUTO.':
        '手动止盈 — Admin 覆盖。重置恢复 AUTO。',
    'AUTO Stop = Entry × (1 − Stop%). Click to override (Admin).':
        'AUTO 止损 = 入场价 × (1 − 止损%)。点击覆盖（仅 Admin）。',
    'AUTO Take Profit = Entry × (1 + Take%). Click to override (Admin).':
        'AUTO 止盈 = 入场价 × (1 + 止盈%)。点击覆盖（仅 Admin）。',
    'Reset to AUTO':
        '重置为 AUTO',
    'Reward':
        '收益',
    'Invalid LONG levels':
        '无效多头价位',
    'Blocked invalid Stop/Take (LONG requires Stop < Entry < Take): {detail}':
        '已拦截无效 Stop/Take（多头需 Stop < 入场价 < Take）：{detail}',
    'Simulation only — never places real brokerage orders. Stop / Take Profit % are fixed at paper-order entry and not recalculated daily.':
        '仅模拟 — 从不下真实券商订单。止损/止盈百分比在开仓时固定，不会按每日市价重算。',
    'Stop Loss % must be between 0.5 and 50':
        '止损 % 须在 0.5–50 之间',
    'Take Profit % must be between 0.5 and 100':
        '止盈 % 须在 0.5–100 之间',
    'AI Candidates refreshed: {n} names for {day}':
        'AI 候选已刷新：{day} 共 {n} 只',
    'Paper orders created: {n} · skipped {s}':
        '已创建模拟订单：{n} · 跳过 {s}',
    'No paper orders created · skipped {s}. Blocked names did not pass.':
        '未创建任何模拟订单 · 跳过 {s}。被拦截的标的未通过。',
    'Blocked — insufficient cash (cannot open): {detail}':
        '已拦截 — 现金不足（不能开仓）：{detail}',
    'Blocked — trading limit reached (cannot open): {detail}':
        '已拦截 — 已达交易额度上限（不能开仓）：{detail}',
    'Blocked — already have an open position: {detail}':
        '已拦截 — 已有持仓：{detail}',
    'Skipped — no suggested allocation ($0): {detail}':
        '已跳过 — 无建议仓位（$0）：{detail}',
    'Could not save Stop/Take — check values (Stop < Entry < Take) and try again.':
        '无法保存 Stop/Take — 请检查价位（需 Stop < 入场价 < Take）后重试。',
    'Save failed — check values and try again.':
        '保存失败 — 请检查数值后重试。',
    'WARNING: Manually close this paper position at the latest market price? This cannot be undone. Click OK only if you are sure.':
        '警告：将按最新市价手动平仓该模拟持仓？此操作不可撤销。确认请点确定。',
    'Confirm again to close this paper position. OK = close now.':
        '再次确认平仓该模拟持仓。点确定将立即平仓。',
    'Admin: edit shares. Cost recalculates at entry price. Blocked if cash/limit insufficient.':
        'Admin：可改股数。成本按开仓价重算。现金不足或超额度会拦截。',
    'Admin: edit shares (fractional OK). Cost recalculates at entry price. Blocked if cash/limit insufficient.':
        'Admin：可改股数（允许碎股）。成本按开仓价重算。现金不足或超额度会拦截。',
    'Buy shares — fractional OK (optional; overrides Buy $ if both set)':
        '买入股数 — 允许碎股（可选；若同时填写金额则以股数为准）',
    'Re-buy / Re-open':
        '加买 / 重新持仓',
    'Add / Re-entry':
        '加仓 / 重新入场',
    'View All':
        '查看全部',
    'Collapse':
        '收起',
    'of':
        '/',
    'candidates':
        '个候选',
    'Re-enter':
        '重新入场',
    'Recently closed (last 63 trading days), not currently held. Shows the Top 8 by current relevance; View All keeps the full pool. Re-enter opens a new trade at current price / AI Score / Knife / Stop / Take — never reuses the old trade. Manual only.':
        '最近 63 个交易日内已平仓、且当前未持仓。默认按当前相关性显示 Top 8；「查看全部」保留完整候选池。重新入场会按现价 / AI Score / 刀口 / 止损 / 止盈开新仓，绝不复用旧单。仅手动操作。',
    'Re-enter {ticker} as a new trade at the current price, AI Score, Knife Risk, Stop and Take? Cash and trading limit still apply. The old closed trade stays in History.':
        '以现价、当前 AI Score、刀口风险、止损与止盈为 {ticker} 开一笔全新交易？仍受现金与交易额度限制。旧平仓记录保留在 History。',
    'Re-entry available: use Re-enter for {ticker} under Add / Re-entry.':
        '可重新入场：请在「加仓 / 重新入场」中对 {ticker} 点击「重新入场」。',
    'Re-entry opened: {ticker} · {shares} sh @ {price} · cost {cost}':
        '已重新入场：{ticker} · {shares} 股 @ {price} · 成本 {cost}',
    'Re-buy':
        '重新持仓',
    'After a manual exit (or any close), use Re-buy to open again at the latest price with the same share count and Stop/Take %. Blocked if cash or trading limit is insufficient, or if already open.':
        '手动平仓（或任意平仓）后，可用「重新持仓」按最新价、相同股数与止损/止盈%再次开仓。现金不足、超额度或已有持仓时会拦截。',
    'Re-buy available: use the Re-buy button for {ticker} to open again.':
        '可重新持仓：请用 {ticker} 的「重新持仓」按钮再次开仓。',
    'Re-buy opened: {ticker} · {shares} sh @ {price} · cost {cost}':
        '已重新持仓：{ticker} · {shares} 股 @ {price} · 成本 {cost}',
    'Re-buy {ticker} now at the latest market price with the same shares? Cash and trading limit still apply.':
        '按最新市价、相同股数重新持仓 {ticker}？仍受现金与交易额度限制。',
    'Re-buy at the latest market price with the same shares? Cash and trading limit still apply.':
        '按最新市价、相同股数重新持仓？仍受现金与交易额度限制。',
    'Stop Loss — AUTO from entry %; Admin can override. Reset restores AUTO.':
        '止损 — 默认来自开仓时百分比；仅 Admin 可覆盖。重置恢复 AUTO。',
    'Take Profit — AUTO from entry %; Admin can override. Reset restores AUTO.':
        '止盈 — 默认来自开仓时百分比；仅 Admin 可覆盖。重置恢复 AUTO。',
    'AUTO Stop from entry %. Click to override (Admin).':
        'AUTO 止损来自开仓百分比。点击覆盖（仅 Admin）。',
    'AUTO Take Profit from entry %. Click to override (Admin).':
        'AUTO 止盈来自开仓百分比。点击覆盖（仅 Admin）。',
    'Priority ⭐ — Admin human flag on AI Trading. Boosts ranking attention; does not change AI Score. Mark below or clear from the Priority list.':
        '优先 ⭐ — AI Trading 上的人工标记。提升排序关注度，不改变 AI Score。可在下方标记或从优先列表清除。',
    'Trade Candidate ★ — Marked on Candidate Analysis (separate from My Watchlist). Research flag only; does not auto-create paper orders.':
        '交易候选 ★ — 在候选分析中标记（与我的自选分开）。仅为研究标记，不会自动创建纸上订单。',
    'Daily paper update done: closed {c}, marked {m}, candidates {n}':
        '每日模拟更新完成：平仓 {c}，标记 {m}，候选 {n}',
    'Daily paper update done: closed {c}, marked {m}, candidates {n}, auto-bought {a}':
        '每日模拟更新完成：平仓 {c}，标记 {m}，候选 {n}，自动买入 {a}',
    'After Stop/Take: auto-buy the highest-ranked AI name not yet used in this experiment (one new position per exit).':
        '止损/止盈后：自动买入本实验尚未用过、AI 排名最高的股票（每平仓 1 只买入 1 只）。',
    'Create Paper Orders is manual for the initial book. After Stop/Take, unused top-ranked names can auto-fill (see Settings).':
        '首次建仓需手动“创建模拟订单”。止损/止盈后可用未用过的高排名股票自动补仓（见设置）。',
    'Ranked by AI Score; Priority Buy ⭐ only reorders allocation. Create Paper Orders is manual for the initial book. After Stop/Take, unused top-ranked names can auto-fill (see Settings). Rising Now / 5D are timing references only.':
        '按 AI Score 排序；优先买入 ⭐ 只影响建议仓位顺序。首次建仓需手动创建模拟订单。止损/止盈后可自动补入尚未用过的高排名标的（见设置）。Rising Now / 5D 仅作时机参考。',
    'Priority marked: {tickers}':
        '已标记优先：{tickers}',
    'Priority cleared: {ticker}':
        '已清除优先：{ticker}',
    'Priority Buy marked: {tickers}':
        '已标记优先买入：{tickers}',
    'Priority Buy cleared: {ticker}':
        '已清除优先买入：{ticker}',
    'Manual exit: {ticker} · P&L {pnl}':
        '手动平仓：{ticker} · 盈亏 {pnl}',
    'Paper Trading action failed: {exc}':
        '模拟交易操作失败：{exc}',
    'Unknown action':
        '未知操作',
    'Enter valid tickers':
        '请输入有效代码',
    'Performance Summary':
        '绩效摘要',
    'Ending Equity':
        '期末权益',
    'Winning Trades':
        '盈利笔数',
    'Losing Trades':
        '亏损笔数',
    'Average Gain %':
        '平均盈利 %',
    'Average Loss %':
        '平均亏损 %',
    'Profit Factor':
        '盈亏比',
    'Max Drawdown %':
        '最大回撤 %',
    'Portfolio Equity Curve':
        '组合权益曲线',
    'Portfolio Equity':
        '组合权益',
    'No equity snapshots yet. Snapshots are saved on each daily paper update.':
        '尚无权益快照。每日模拟更新时会保存快照。',
    'Daily Performance':
        '每日表现',
    'No daily equity rows yet.':
        '尚无每日权益记录。',
    'Date':
        '日期',
    'Trades Closed':
        '平仓笔数',
    'Wins':
        '盈利',
    'Losses':
        '亏损',
    'Daily Return %':
        '日回报 %',
    'Open Position Value':
        '持仓市值',
    'Total Equity':
        '总权益',
    'Exit Analysis':
        '平仓分析',
    'No closed trades in this range yet.':
        '该时间范围内尚无已平仓交易。',
    'Trades':
        '笔数',
    'Total P&L':
        '总盈亏',
    'Avg Return':
        '平均回报',
    'Individual Trade History':
        '逐笔交易历史',
    'Holding Days = calendar days (Exit Date − Entry Date). Entry research fields are frozen at open and never overwritten.':
        '持有天数 = 日历日（平仓日 − 开仓日）。开仓时的研究字段会永久冻结，后续不会被覆盖。',
    'Holding Days':
        '持有天数',
    '63D Position at Entry':
        '开仓 63D 位置',
    'Financial Score at Entry':
        '开仓财报评分',
    'News at Entry':
        '开仓新闻',
    'Saved: SMA={sma}, rebound lookback={rebound}. Auto: universe weekly '
    '{weekday} {uh:02d}:{um:02d} PT; prices weekdays {ph:02d}:{pm:02d} PT '
    'after US close. Paper SL −{stop}% / TP +{take}%. Restart app for in-app '
    'schedule; Windows tasks use install-time values.':
        '已保存：SMA={sma}，反弹回看={rebound}。自动：股票池每周 {weekday} {uh:02d}:{um:02d} PT；'
        '行情工作日收盘后 {ph:02d}:{pm:02d} PT。模拟止损 −{stop}% / 止盈 +{take}%。'
        '应用内定时需重启；Windows 任务使用安装时设定。',
}


def get_lang() -> str:
    lang = (session.get(SESSION_LANG_KEY) or DEFAULT_LANG).lower()
    return lang if lang in LANGS else DEFAULT_LANG


def set_lang(lang: str) -> str:
    lang = (lang or "").lower()
    if lang not in LANGS:
        lang = DEFAULT_LANG
    session[SESSION_LANG_KEY] = lang
    return lang


def gettext(message: str) -> str:
    """Translate msgid (English) to the active UI language."""
    if not message:
        return message
    if get_lang() == "zh":
        return ZH.get(message, message)
    return message


def ngettext_format(message: str, **kwargs) -> str:
    text = gettext(message)
    try:
        return text.format(**kwargs)
    except Exception:
        return text


def _parse_ui_local_dt(value: Any):
    """Parse a timestamp into America/Los_Angeles local datetime, or None."""
    if value is None or value == "":
        return None
    try:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
    except Exception:
        return None

    raw = str(value).strip()
    dt: datetime | None = None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw[:19], fmt)
                break
            except Exception:
                continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:
        return dt.astimezone()


def format_ui_datetime(value: Any, *, lang: str | None = None) -> str:
    """
    Human-readable local date + time for UI.
    EN: Aug 18, 2026 5:42 PM
    ZH: 2026年8月18日 17:42
    Avoids raw ISO/UTC strings in the UI.
    """
    local = _parse_ui_local_dt(value)
    if local is None:
        return "" if value is None or value == "" else str(value).strip()

    use_lang = (lang or get_lang()).lower()
    if use_lang == "zh":
        return f"{local.year}年{local.month}月{local.day}日 {local.hour:02d}:{local.minute:02d}"
    hour12 = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"{local.strftime('%b')} {local.day}, {local.year} {hour12}:{local.minute:02d} {ampm}"


def format_ui_date(value: Any, *, lang: str | None = None) -> str:
    """Date-only local string for multi-line Updated cards."""
    local = _parse_ui_local_dt(value)
    if local is None:
        return ""
    use_lang = (lang or get_lang()).lower()
    if use_lang == "zh":
        return f"{local.year}年{local.month}月{local.day}日"
    return f"{local.strftime('%b')} {local.day}, {local.year}"


def format_ui_time(value: Any, *, lang: str | None = None) -> str:
    """Time-only local string for multi-line Updated cards."""
    local = _parse_ui_local_dt(value)
    if local is None:
        return ""
    use_lang = (lang or get_lang()).lower()
    if use_lang == "zh":
        return f"{local.hour:02d}:{local.minute:02d}"
    hour12 = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"{hour12}:{local.minute:02d} {ampm}"


def tab_description(key: str, *, mine_list_label: str = "", can_edit_mine: bool = False) -> str:
    """Localized watchlist tab blurb."""
    lang = get_lang()
    label = mine_list_label or "—"
    if key == "setup":
        return ZH["desc_setup_zh"] if lang == "zh" else ZH["desc_setup"]
    if key == "low_target":
        return ZH["desc_low_target_zh"] if lang == "zh" else ZH["desc_low_target"]
    if key == "low_63d":
        return ZH["desc_low_63d_zh"] if lang == "zh" else ZH["desc_low_63d"]
    if key == "rising_now":
        return ZH["desc_rising_now_zh"] if lang == "zh" else ZH["desc_rising_now"]
    if key == "multi_signal":
        return ZH["desc_multi_signal_zh"] if lang == "zh" else ZH["desc_multi_signal"]
    if key == "temp":
        return ZH["desc_temp_zh"] if lang == "zh" else ZH["desc_temp"]
    if key == "mine":
        if lang == "zh":
            src = ZH["desc_mine_owner_zh"] if can_edit_mine else ZH["desc_mine_public_zh"]
        else:
            src = ZH["desc_mine_owner"] if can_edit_mine else ZH["desc_mine_public"]
        return src.format(list=label)
    return ""
