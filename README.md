# 🤖 IBKR Algorithmic Trading System

Système de trading algorithmique multi-stratégies connecté à **Interactive Brokers** via l'API officielle TWS.  
Fonctionne en **paper trading 24/7** sur les cryptomonnaies (BTC, ETH, LTC, BCH via PAXOS).

---

## 📁 Structure du projet

```
Tools/
├── main.py                  # Point d'entrée et orchestrateur principal
├── config.py                # Configuration centralisée (assets, stratégies, risque)
├── requirements.txt         # Dépendances Python
├── diag_crypto.py           # Script de diagnostic des données de marché IBKR
│
├── core/
│   ├── broker.py            # Connexion IBKR (EClient/EWrapper), ordres, ticks
│   ├── data_feed.py         # Récupération et normalisation des données de marché
│   ├── indicators.py        # Indicateurs techniques (RSI, MACD, BB, ATR…)
│   ├── portfolio.py         # Suivi du portefeuille et des positions
│   └── risk_manager.py      # Gestion du risque (drawdown, exposition, halt)
│
├── strategies/
│   ├── base_strategy.py     # Classe abstraite BaseStrategy + TradeSignal
│   ├── momentum_strategy.py # Stratégie momentum (RSI + MACD, multi-timeframe)
│   ├── mean_reversion.py    # Stratégie mean reversion (Bollinger Bands)
│   └── breakout_strategy.py # Stratégie breakout (range + volume spike)
│
├── agents/
│   ├── base_agent.py        # Classe abstraite BaseAgent
│   ├── market_analyst.py    # Détection de régime de marché (trending/ranging/volatile)
│   ├── risk_agent.py        # Validation des signaux avant exécution
│   └── optimizer_agent.py   # Optimisation périodique des paramètres
│
├── execution/
│   ├── order_manager.py     # Envoi et suivi des ordres IBKR
│   └── position_tracker.py  # Gestion des exits (TP, SL, trailing, time-based)
│
├── analytics/
│   ├── performance.py       # Calcul des métriques (Sharpe, drawdown, win rate…)
│   └── reporter.py          # Génération des rapports de performance
│
├── database/
│   ├── models.py            # Modèles SQLAlchemy (trades, signaux, snapshots)
│   └── repository.py        # Accès aux données (TradeRepository, SignalRepository…)
│
└── tests/
    └── test_strategies.py   # 23 tests unitaires des stratégies
```

---

## ⚙️ Installation

### 1. Prérequis

- Python **3.11+** (testé sur 3.14)
- **IB Gateway** ou **TWS** en mode paper trading (port `7497`)
- **TWS API v10.30+** — la version PyPI (`ibapi 9.81.1`) est trop ancienne pour les cryptos

### 2. Installer ibapi v10.30 (obligatoire)

```bash
# Télécharger le ZIP depuis le site IBKR
# https://interactivebrokers.github.io/downloads/twsapi_macunix.1030.01.zip

# Extraire et installer
cd IBJts/source/pythonclient
python setup.py install
```

> ⚠️ La version PyPI (`pip install ibapi`) est bloquée à **9.81.1** et ne supporte pas les cryptomonnaies (erreur `10285` — protocole < 163).

### 3. Installer les autres dépendances

```bash
pip install -r requirements.txt
```

### 4. Configurer IB Gateway

- Lancer **IB Gateway** en mode **Paper Trading** sur le port `7497`
- Activer les connexions API : `Settings > API > Enable ActiveX and Socket Clients`
- Laisser coché "Read-Only API" si vous ne voulez pas passer d'ordres réels

---

## 🚀 Lancement

```bash
python main.py
```

Le système va :
1. Se connecter à IBKR (port 7497)
2. Basculer sur les données **delayed** (type 3, gratuites)
3. Charger l'historique BTC/ETH/LTC/BCH
4. Démarrer la boucle principale (cycle toutes les 5 secondes)
5. Générer des signaux, vérifier le risque, passer des ordres

Les logs sont affichés en console et sauvegardés dans `trading.log`.  
La base de données SQLite est créée automatiquement dans `trading.db`.

---

## ⚡ Assets configurés (24/7)

| Symbole | Type   | Exchange | Devise |
|---------|--------|----------|--------|
| BTC     | CRYPTO | PAXOS    | USD    |
| ETH     | CRYPTO | PAXOS    | USD    |
| LTC     | CRYPTO | PAXOS    | USD    |
| BCH     | CRYPTO | PAXOS    | USD    |

> Pour revenir sur des actions US ou des futures, modifiez la liste `assets` dans `config.py`.

---

## 📈 Stratégies

| Stratégie        | Signal d'entrée                              | Exit                          |
|------------------|----------------------------------------------|-------------------------------|
| **Momentum**     | RSI > 60 + MACD haussier, multi-timeframe    | Trailing stop 1.5% ou RSI > 80 |
| **Mean Reversion** | Prix sous la bande basse de Bollinger (2σ) | Retour à la moyenne (MM20)   |
| **Breakout**     | Cassure du range N bougies + volume spike x2 | TP 2:1 risk/reward ou 8h max  |

Toutes les stratégies passent d'abord par le **RiskAgent** (vérification drawdown, exposition, corrélation) avant exécution.

---

## 🛡️ Gestion du risque

Paramètres configurables dans `config.py` → `RiskConfig` :

| Paramètre                    | Défaut | Description                          |
|------------------------------|--------|--------------------------------------|
| `max_daily_drawdown_pct`     | 3%     | Halt du trading si dépassé           |
| `max_exposure_per_asset_pct` | 5%     | Exposition max par actif             |
| `max_simultaneous_positions` | 5      | Positions ouvertes simultanées       |
| `max_correlation_threshold`  | 0.75   | Évite les positions trop corrélées   |

---

## 🧪 Tests

```bash
pytest tests/ -v
```

23 tests unitaires couvrent les 3 stratégies (signaux, sizing, exits, edge cases).

---

## 🔧 Diagnostic

En cas de problème avec les données de marché :

```bash
python diag_crypto.py
```

Ce script teste tous les types de données historiques (`MIDPOINT`, `AGGTRADES`, `BID`, `ASK`…) pour BTC et affiche les barres reçues et les erreurs IBKR.

---

## 🔄 Rapport de performance

```bash
python -c "
from analytics.reporter import Reporter
from analytics.performance import PerformanceCalculator
from database.models import create_db_engine, create_session
from database.repository import TradeRepository
engine = create_db_engine()
session = create_session(engine)
repo = TradeRepository(session)
perf = PerformanceCalculator(repo)
rep = Reporter(perf, repo)
print(rep.generate_report())
"
```

---

## ⚠️ Avertissements IBKR connus (non bloquants)

| Code  | Message                              | Impact    |
|-------|--------------------------------------|-----------|
| 2104  | Connexion farm data OK               | Aucun     |
| 2106  | Connexion HMDS OK                    | Aucun     |
| 10089 | Pas d'abonnement données live        | Aucun — données delayed utilisées |
| 10167 | Données delayed affichées            | Aucun — informatif |
| 10285 | Fractional sizes (ibapi < v163)      | Résolu avec ibapi 10.30 |
| 10299 | Suggestion d'utiliser AGGTRADES      | Aucun — on utilise déjà AGGTRADES |

---

## 📌 Notes importantes

- **Ne jamais utiliser en live sans validation prolongée en paper.**
- Pour passer en **live**, changer le port dans `config.py` : `ibkr_port: int = 7496`
- Pour des données **temps réel** (si abonnement IBKR) : `market_data_type: int = 1`
- L'expiry des contrats futures est calculée automatiquement (`_next_futures_expiry()` dans `config.py`)

