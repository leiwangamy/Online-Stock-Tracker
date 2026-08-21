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
        "SMA 提醒：默认价 = SMA×0.95（随均线更新）。人工价覆盖生效价直至「重置」。🟡 WATCH = 距生效价 ≤5%；🟢 ALERT/DEEP 进入提醒区。绝不会自动下单。",
    "Default Alert = SMA × 0.95. Updates whenever SMA refreshes. Not a buy signal.":
        "默认提醒价 = SMA × 0.95。随 SMA 刷新而更新。不是买入信号。",
    "Manual Alert — your override. Stays fixed until you edit or Reset to Default.":
        "人工提醒价 — 你的覆盖值。固定不变，直到你修改或「重置为默认」。",
    "Active Alert = Manual if set, else Default. Used for ALERT status.":
        "生效提醒价 = 有人工价用人工价，否则用默认价。用于 ALERT 状态判断。",
    "WATCH ≤ SMA · ALERT ≤ Active · DEEP ≤ SMA×0.90. Research zones only — never auto-buy.":
        "WATCH ≤ Active×1.05 · ALERT ≤ 生效价 · DEEP ≤ SMA×0.90。仅为研究区，绝不自动买入。",
    "WATCH ≤ Active×1.05 · ALERT ≤ Active · DEEP ≤ SMA×0.90. Research zones only — never auto-buy.":
        "WATCH ≤ Active×1.05 · ALERT ≤ 生效价 · DEEP ≤ SMA×0.90。仅为研究区，绝不自动买入。",
    "Manual Alert. Enter/blur to save; clear or Reset to use Default (SMA×0.95). Does not trigger trades.":
        "人工提醒价。回车/失焦保存；清空或点「重置」则改回默认（SMA×0.95）。不触发交易。",
    "Reset to Default — clear Manual; Active = current SMA × 0.95":
        "重置为默认 — 清除人工价；生效价 = 当前 SMA × 0.95",
    "Active = Manual Alert": "生效 = 人工提醒价",
    "Active = Default Alert (SMA × 0.95)": "生效 = 默认提醒价（SMA × 0.95）",
    "DEEP — Price ≤ SMA × 0.90. Research zone only; not an auto BUY.":
        "DEEP — 现价 ≤ SMA × 0.90。仅为研究区，不是自动买入。",
    "ALERT — Price ≤ Active Alert. Research zone only; not an auto BUY.":
        "ALERT — 现价 ≤ 生效提醒价。仅为研究区，不是自动买入。",
    "WATCH — Price ≤ SMA. Research zone only; not an auto BUY.":
        "WATCH — 现价在生效提醒价上方 5% 内（≤ Active×1.05）。仅为研究区，不是自动买入。",
    "WATCH — within 5% above Active Alert (≤ Active×1.05). Research zone only; not an auto BUY.":
        "WATCH — 现价在生效提醒价上方 5% 内（≤ Active×1.05）。仅为研究区，不是自动买入。",
    "Above SMA — no alert zone": "高于提醒区 — 无黄/绿点",
    "Distance": "距离",
    "Short-term SMA used for Default / Deep alerts": "用于默认/深度提醒的短期均线",
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
    "Deduplicated research universe combining valuation, position, momentum, strength and financial signals. Human My Watchlist and Trade Candidate flags. AI Score unchanged.":
        "去重研究宇宙：汇总估值、仓位、动量、强势与财报信号。人工「我的自选」与 Trade Candidate 标记。不改 AI Score。",
    "Deduplicated research universe combining valuation, position, momentum, strength and financial signals. Add to My Watchlist or mark Trade Candidate manually — nothing auto-selects for AI Trading.":
        "去重研究宇宙：汇总估值、仓位、动量、强势与财报信号。手动加入「我的自选」或标记 Trade Candidate——不会自动进入 AI Trading。",
    "Workflow: Watchlist → Research → My Watchlist / Trade Candidate → AI Trading. Signal ≠ research decision ≠ trade decision.":
        "流程：观察列表 → 研究中心 → 我的自选 / Trade Candidate → AI Trading。信号 ≠ 研究决策 ≠ 交易决策。",
    "Match ≥ 2 across Oversold / Target &lt;80% / 63D Low / Rising Now. Strong is a separate indicator.":
        "在超卖 / Target <80% / 63D 低位 / 正在上涨 中匹配 ≥2。Strong 为独立指示。",
    "Up Days ≥ 3/5 · 5D Return ≥ +3%. Dynamic daily group; no retention.":
        "上涨天数 ≥ 3/5 · 5日涨幅 ≥ +3%。每日动态分组，无留存。",
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
        "Long-term My Watchlist (current: {list}). SMA alerts: Default=SMA×0.95 (auto), optional Manual override. Signed in: edit list & alerts; Est.Value / MOS / CLV visible.",
    "desc_mine_owner_zh":
        "长期观察 / 我的自选（当前：{list}）。SMA 提醒：默认=SMA×0.95（自动），可人工覆盖。已登录：可增删自选、改提醒，并显示 Est.Value / MOS / CLV。",
    "desc_mine_public":
        "Long-term My Watchlist (current: {list}). SMA Default/Active alerts shown; public page hides Est.Value / MOS / CLV; sign in to edit list & Manual Alert.",
    "desc_mine_public_zh":
        "长期观察 / 我的自选（当前：{list}）。显示 SMA 默认/生效提醒；公开页不显示 Est.Value / MOS / CLV；登录后可改自选与人工提醒价。",
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
    'Stock markets are inherently unpredictable and involve risk. This project is intended solely for educational, research, and experimental purposes. Users are encouraged to use <strong>Paper Trading</strong> or approximately <strong>CAD/USD 100</strong> in small experimental capital through <strong>Fractional Shares</strong>. Any data, analysis, valuation, scoring, or other information provided by this project <strong>does not constitute investment advice</strong>. Users are solely responsible for their own investment decisions and risks.':
        '股市风险莫测。本项目仅供教学、研究及实验使用，建议仅使用 <strong>Paper Trading（模拟交易）</strong>，或以约 <strong>100 加元/美元</strong>的小额资金通过 <strong>Fractional Shares（碎股）</strong>进行实验。本项目所提供的任何数据、分析、估值、评分或其他信息均<strong>不构成投资建议</strong>。投资者应自行判断并承担投资风险。',
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
    'Daily paper update done: closed {c}, marked {m}, candidates {n}':
        '每日模拟更新完成：平仓 {c}，标记 {m}，候选 {n}',
    'Priority marked: {tickers}':
        '已标记优先：{tickers}',
    'Priority cleared: {ticker}':
        '已清除优先：{ticker}',
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
