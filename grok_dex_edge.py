#!/usr/bin/env python3
"""
Grok DEX Profit Edge Scanner v2
Improved scoring + richer risk analysis + better multi-chain support
"""

import requests
import argparse
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
import math

# ====================== CORE ANALYZER ======================

def fetch_dexscreener(chain: str, token: str) -> Optional[Dict]:
    """Fetch best pair for a token from DexScreener"""
    url = f"https://api.dexscreener.com/latest/dex/tokens/{token}"
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        data = r.json()
        pairs = data.get("pairs") or []
        chain = chain.lower().strip()
        # Prefer exact chain match
        filtered = [p for p in pairs if p.get("chainId", "").lower() == chain]
        if not filtered:
            filtered = pairs  # fallback
        if not filtered:
            return None
        # Rank by liquidity
        filtered.sort(key=lambda x: float(x.get("liquidity", {}).get("usd") or 0), reverse=True)
        return filtered[0]
    except Exception as e:
        print(f"API error: {e}")
        return None


def fetch_all_pairs(chain: str, token: str) -> List[Dict]:
    """Return all pairs sorted by liquidity"""
    url = f"https://api.dexscreener.com/latest/dex/tokens/{token}"
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        pairs = r.json().get("pairs") or []
        chain = chain.lower().strip()
        filtered = [p for p in pairs if p.get("chainId", "").lower() == chain] or pairs
        filtered.sort(key=lambda x: float(x.get("liquidity", {}).get("usd") or 0), reverse=True)
        return filtered
    except:
        return []


def compute_profit_edge(pair: Dict) -> Dict[str, Any]:
    """
    Improved Profit Edge Score (0-100)
    Weighted for real edge factors used by profitable on-chain traders.
    """
    if not pair:
        return {"score": 0, "grade": "F", "flags": ["No data"], "metrics": {}, "risk_level": "Unknown"}

    # --- Extract raw metrics ---
    liq = float(pair.get("liquidity", {}).get("usd") or 0)
    vol_h24 = float(pair.get("volume", {}).get("h24") or 0)
    vol_h6 = float(pair.get("volume", {}).get("h6") or 0)
    vol_h1 = float(pair.get("volume", {}).get("h1") or 0)

    pc_h24 = float(pair.get("priceChange", {}).get("h24") or 0)
    pc_h6 = float(pair.get("priceChange", {}).get("h6") or 0)
    pc_h1 = float(pair.get("priceChange", {}).get("h1") or 0)
    pc_m5 = float(pair.get("priceChange", {}).get("m5") or 0)

    txns_h24 = pair.get("txns", {}).get("h24", {})
    buys_h24 = int(txns_h24.get("buys") or 0)
    sells_h24 = int(txns_h24.get("sells") or 0)
    total_tx_h24 = buys_h24 + sells_h24
    buy_ratio = (buys_h24 / total_tx_h24 * 100) if total_tx_h24 > 0 else 50.0

    txns_h1 = pair.get("txns", {}).get("h1", {})
    buys_h1 = int(txns_h1.get("buys") or 0)
    sells_h1 = int(txns_h1.get("sells") or 0)

    created = pair.get("pairCreatedAt")
    age_hours = None
    if created:
        try:
            age_hours = (datetime.now(timezone.utc).timestamp() * 1000 - float(created)) / 3_600_000
        except:
            pass

    fdv = float(pair.get("fdv") or 0)
    market_cap = float(pair.get("marketCap") or 0)

    # --- Derived ---
    vol_liq = vol_h24 / liq if liq > 0 else 0
    volume_acceleration = (vol_h1 * 24) / vol_h24 if vol_h24 > 0 else 0  # >1 means accelerating

    score = 0.0
    flags: List[str] = []
    positive_signals: List[str] = []

    # ========== 1. Liquidity Quality (0-22) ==========
    if liq >= 1_000_000:
        score += 22
        positive_signals.append("Strong liquidity (> $1M)")
    elif liq >= 300_000:
        score += 18
    elif liq >= 100_000:
        score += 14
    elif liq >= 40_000:
        score += 9
    elif liq >= 15_000:
        score += 5
    else:
        score += 1
        flags.append("Very low liquidity — high slippage & rug risk")

    # ========== 2. Volume Conviction (0-18) ==========
    if vol_liq >= 8:
        score += 18
        positive_signals.append("Extremely high volume relative to liquidity")
    elif vol_liq >= 3:
        score += 14
    elif vol_liq >= 1.2:
        score += 10
    elif vol_liq >= 0.4:
        score += 5
    else:
        flags.append("Low volume / liquidity ratio (stale or dead interest)")

    # ========== 3. Buy Pressure & Order Flow (0-18) ==========
    if buy_ratio >= 68 and total_tx_h24 > 50:
        score += 18
        positive_signals.append("Strong buy dominance")
    elif buy_ratio >= 58:
        score += 13
    elif buy_ratio >= 52:
        score += 8
    elif buy_ratio < 42:
        score += 0
        flags.append("Heavy sell pressure (possible distribution)")
    else:
        score += 4

    # Recent 1h buy pressure bonus
    if buys_h1 + sells_h1 > 10:
        recent_buy_ratio = buys_h1 / (buys_h1 + sells_h1) * 100
        if recent_buy_ratio > 65:
            score += 3
            positive_signals.append("Strong recent (1h) buy pressure")

    # ========== 4. Momentum Quality (0-15) ==========
    # Prefer clean upward momentum without extreme parabolic risk
    if 5 < pc_h1 < 40 and pc_h6 > 0:
        score += 12
        positive_signals.append("Healthy short-term momentum")
    elif pc_h6 > 25 and pc_h24 > 40:
        score += 8
    elif pc_h24 > 100:
        score += 4
        flags.append("Parabolic move — elevated reversal risk")
    elif pc_h24 < -25:
        flags.append("Significant 24h dump")
    elif pc_h1 < -12:
        flags.append("Sharp recent dump")

    # ========== 5. Activity & Organic Interest (0-12) ==========
    if total_tx_h24 >= 2000:
        score += 12
    elif total_tx_h24 >= 600:
        score += 9
    elif total_tx_h24 >= 200:
        score += 6
    elif total_tx_h24 >= 50:
        score += 3
    else:
        flags.append("Very low transaction count")

    # ========== 6. Age Factor (0-10) ==========
    if age_hours is not None:
        if 2 <= age_hours <= 36:
            score += 10
            positive_signals.append("Ideal age window (early but not brand new)")
        elif 0.5 <= age_hours < 2:
            score += 6
            flags.append("Very new pair — higher rug probability")
        elif age_hours < 0.5:
            score += 2
            flags.append("Extremely new (<30 min) — extreme caution")
        elif 36 < age_hours <= 168:  # up to 1 week
            score += 5
        else:
            score += 2  # older pairs get less edge score

    # ========== 7. Volume Acceleration Bonus (0-5) ==========
    if volume_acceleration > 1.8 and vol_h1 > 1000:
        score += 5
        positive_signals.append("Volume accelerating")

    # Cap and finalize
    score = max(0, min(100, round(score)))

    # Grade
    if score >= 82:
        grade = "A+ Strong Edge"
    elif score >= 70:
        grade = "A Good Edge"
    elif score >= 58:
        grade = "B Decent Setup"
    elif score >= 45:
        grade = "C Neutral / Watch"
    else:
        grade = "D/F Weak or High Risk"

    # Overall risk level
    risk_score = 0
    if liq < 20_000: risk_score += 3
    if age_hours and age_hours < 2: risk_score += 2
    if buy_ratio < 40: risk_score += 2
    if vol_liq < 0.3: risk_score += 1
    if pc_h24 < -30: risk_score += 2

    if risk_score >= 5:
        risk_level = "HIGH"
    elif risk_score >= 3:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW-MODERATE"

    metrics = {
        "liquidity_usd": liq,
        "volume_24h": vol_h24,
        "volume_1h": vol_h1,
        "vol_liq_ratio": round(vol_liq, 2),
        "volume_acceleration": round(volume_acceleration, 2),
        "buy_ratio": round(buy_ratio, 1),
        "buys_24h": buys_h24,
        "sells_24h": sells_h24,
        "buys_1h": buys_h1,
        "sells_1h": sells_h1,
        "price_change_m5": pc_m5,
        "price_change_1h": pc_h1,
        "price_change_6h": pc_h6,
        "price_change_24h": pc_h24,
        "age_hours": round(age_hours, 1) if age_hours is not None else None,
        "fdv": fdv,
        "market_cap": market_cap,
        "price_usd": pair.get("priceUsd"),
        "pair_address": pair.get("pairAddress"),
        "dex": pair.get("dexId"),
        "url": pair.get("url"),
        "base_symbol": pair.get("baseToken", {}).get("symbol"),
        "quote_symbol": pair.get("quoteToken", {}).get("symbol"),
        "chain": pair.get("chainId"),
        "base_address": pair.get("baseToken", {}).get("address"),
    }

    return {
        "score": score,
        "grade": grade,
        "risk_level": risk_level,
        "flags": flags,
        "positive_signals": positive_signals,
        "metrics": metrics,
        "pair": pair
    }


def generate_links(chain: str, token: str, pair: Dict) -> Dict[str, str]:
    chain = chain.lower()
    token = token.strip()
    pair_addr = pair.get("pairAddress", "")
    base = pair.get("baseToken", {}).get("address", token)

    links = {
        "DexScreener": pair.get("url") or f"https://dexscreener.com/{chain}/{pair_addr}",
        "GeckoTerminal": f"https://www.geckoterminal.com/{chain}/pools/{pair_addr}",
    }

    if chain in ("solana", "sol"):
        links["GMGN Smart Money + KOL"] = f"https://gmgn.ai/sol/token/{base}"
        links["Birdeye"] = f"https://birdeye.so/token/{base}?chain=solana"
        links["Bubblemaps"] = f"https://app.bubblemaps.io/sol/token/{base}"
        links["Solscan"] = f"https://solscan.io/token/{base}"
    elif chain in ("ethereum", "eth"):
        links["Deep Blue Alpha (Whales)"] = "https://deepbluealpha.io"
        links["GMGN"] = f"https://gmgn.ai/eth/token/{base}"
        links["Arkham"] = f"https://platform.arkhamintelligence.com/explorer/token/{base}"
        links["Bubblemaps"] = f"https://app.bubblemaps.io/eth/token/{base}"
        links["Etherscan"] = f"https://etherscan.io/token/{base}"
    elif chain == "base":
        links["GMGN"] = f"https://gmgn.ai/base/token/{base}"
        links["Arkham"] = f"https://platform.arkhamintelligence.com/explorer/token/{base}"
        links["Bubblemaps"] = f"https://app.bubblemaps.io/base/token/{base}"
        links["Basescan"] = f"https://basescan.org/token/{base}"
    elif chain in ("bsc", "bnb"):
        links["GMGN"] = f"https://gmgn.ai/bsc/token/{base}"
        links["BscScan"] = f"https://bscscan.com/token/{base}"
    else:
        links["GMGN"] = f"https://gmgn.ai/{chain}/token/{base}"
        links["Arkham"] = f"https://platform.arkhamintelligence.com/explorer/token/{base}"

    links["DeBank"] = "https://debank.com"
    return links


def format_report(result: Dict, chain: str, token: str) -> str:
    m = result["metrics"]
    flags = result["flags"]
    positives = result.get("positive_signals", [])

    report = f"""
═══════════════════════════════════════
🚀 GROK DEX PROFIT EDGE SCANNER v2
═══════════════════════════════════════

{m.get('base_symbol', '?')}/{m.get('quote_symbol', '?')}  |  {str(m.get('chain', chain)).upper()}
Price: ${m.get('price_usd', 'N/A')}

📊 PROFIT EDGE SCORE: {result['score']}/100
Grade: {result['grade']}
Risk Level: {result['risk_level']}

─── Core Metrics ───
Liquidity:        ${m['liquidity_usd']:,.0f}
24h Volume:       ${m['volume_24h']:,.0f}
1h Volume:        ${m.get('volume_1h', 0):,.0f}
Vol/Liq Ratio:    {m['vol_liq_ratio']}x
Vol Acceleration: {m.get('volume_acceleration', 0)}x
Buy Pressure:     {m['buy_ratio']}%  ({m['buys_24h']}B / {m['sells_24h']}S)
1h Buys/Sells:    {m.get('buys_1h',0)} / {m.get('sells_1h',0)}
5m / 1h / 6h / 24h: {m.get('price_change_m5',0):+.1f}%  {m['price_change_1h']:+.1f}%  {m['price_change_6h']:+.1f}%  {m['price_change_24h']:+.1f}%
Age:              {m['age_hours']} hours
FDV / MC:         ${m.get('fdv',0):,.0f} / ${m.get('market_cap',0):,.0f}
DEX:              {m.get('dex', 'N/A')}
"""

    if positives:
        report += "\n✅ Positive Signals:\n"
        for p in positives:
            report += f"  • {p}\n"

    if flags:
        report += "\n⚠️ Risk Flags:\n"
        for f in flags:
            report += f"  • {f}\n"

    links = generate_links(chain, token, result.get("pair", {}))
    report += "\n🔗 Smart Money / KOL / Whale Links:\n"
    for name, url in links.items():
        report += f"  • {name}: {url}\n"

    report += """
═══════════════════════════════════════
💡 High score + confirmed smart money / KOL inflow on GMGN or Deep Blue Alpha = real edge.
Always size small and DYOR. Not financial advice.
═══════════════════════════════════════
"""
    return report.strip()


def analyze(chain: str, token: str) -> str:
    pair = fetch_dexscreener(chain, token)
    if not pair:
        return f"❌ No data found for `{token}` on `{chain}`. Double-check the address and chain name."
    result = compute_profit_edge(pair)
    return format_report(result, chain, token)


# ====================== CLI ======================

def main_cli():
    parser = argparse.ArgumentParser(description="Grok DEX Profit Edge Scanner v2")
    parser.add_argument("chain", help="Chain (solana, base, ethereum, bsc...)")
    parser.add_argument("token", help="Token contract / mint address")
    args = parser.parse_args()
    print(analyze(args.chain, args.token))


if __name__ == "__main__":
    main_cli()
