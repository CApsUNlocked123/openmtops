# Disclaimer

**Read this document in full before using OpenMTOps with a live brokerage account or real money.**

---

## 1. Not Financial Advice

OpenMTOps is a **software tool**, not an investment service. Nothing produced by this software — including but not limited to:

- Telegram signals it displays,
- Strategy widget outputs (BUY / NO_TRADE / WAIT, entry / SL / target levels),
- OI walls, max pain, PCR, regime, phase, or any other indicator,
- Auto-generated trade suggestions,

— constitutes investment advice, a recommendation, a solicitation to buy or sell securities, or professional guidance of any kind. It is raw data and algorithmic output presented for your informational use only.

## 2. Not SEBI-Registered

The author of OpenMTOps is **not a SEBI-registered investment advisor, research analyst, or portfolio manager**. This software is released as a personal open-source project under the AGPL-3.0 license. No investment advisory service, subscription product, or managed-account offering is associated with it.

If you want regulated investment advice, consult a SEBI-registered advisor.

## 3. You Are the Trader

When you configure OpenMTOps with your Dhan credentials and enable auto-execution, **you are the trader placing every order**. The software acts strictly as an agent on your behalf. You are solely responsible for:

- Every order that is placed, filled, modified, or cancelled through your account.
- The size of each position, the capital at risk, and the risk management parameters you configure (entry price, stop loss, targets, lot count).
- Monitoring the software while it runs and intervening manually at any time.
- Complying with all applicable laws, exchange rules, and your broker's terms of service.

## 4. Trading Risk

Futures and options (F&O) trading involves **substantial risk of loss** and is not suitable for every investor. Losses can exceed initial margin. You should:

- Only trade with capital you can afford to lose.
- Understand the mechanics of options (strike, expiry, IV, theta, gamma) before using any automation.
- Paper-trade or use the built-in `TESTING=1` mock mode before risking real money.
- Assume that any automated system can and will place wrong orders at some point — from software bugs, network failures, broker API changes, market halts, or bad input data.

## 5. No Guarantee of Performance

**Past results do not guarantee future performance.** Strategy backtests, screenshots, historical win rates, or P&L figures shown anywhere in this project — in the README, in the UI, or in issues / discussions — are examples, not predictions. Market conditions change. An approach that worked in one regime will fail in another.

## 6. No Warranty

OpenMTOps is provided **"AS IS", without warranty of any kind**, express or implied, including but not limited to warranties of merchantability, fitness for a particular purpose, non-infringement, accuracy, reliability, or availability.

In particular, the authors make no warranty that:

- The signals, indicators, or auto-execution logic will produce profitable trades.
- The software will operate without interruption, error, bugs, or data loss.
- The Dhan WebSocket or REST API, the Telegram API, or any other external service will remain compatible with this code.
- Orders will be placed, filled, or exited correctly under all market conditions.

## 7. Limitation of Liability

**To the maximum extent permitted by applicable law**, the authors and contributors of OpenMTOps shall not be liable for any direct, indirect, incidental, special, consequential, exemplary, or punitive damages arising from your use of this software, including but not limited to:

- Trading losses (realized or unrealized),
- Missed trading opportunities,
- Erroneous order placement, partial fills, or exit failures,
- Data loss, credential leakage, or account compromise,
- Any other financial, reputational, or operational harm,

even if the authors have been advised of the possibility of such damage.

## 8. Broker and Exchange Rules Apply

Your use of OpenMTOps does not override any agreement between you and Dhan, the NSE, the BSE, or SEBI. You remain fully bound by:

- Your Dhan client agreement and API terms of service.
- NSE / BSE circulars on algorithmic trading for retail clients.
- SEBI regulations applicable to individual traders in India.

If any of those rules prohibit a particular use of automation, **those rules take precedence over anything this software enables**.

## 9. Security and Credentials

OpenMTOps stores your Dhan access token, Telegram API credentials, and Telethon session on your local machine. You are solely responsible for:

- Keeping those credentials secure.
- Setting the `APP_PIN` and, if you expose the app on a network, running it behind Nginx with TLS and proper access controls.
- Rotating tokens when appropriate (Dhan access tokens expire annually).

The authors have no ability to recover your credentials, your trade history, or your session if any of them are lost, stolen, or corrupted.

## 10. Open Source, No Support Guarantee

OpenMTOps is released under the **AGPL-3.0** license as a community project. There is no SLA, no paid support tier, and no guaranteed response time on issues or pull requests. Use at your own discretion.

---

**By installing, configuring, or running OpenMTOps you acknowledge that you have read this disclaimer in full, understood it, and accept full responsibility for your trading activity and its outcomes.**

If any part of this disclaimer is unclear, or if you do not agree with it, **do not use this software with a live brokerage account**.
