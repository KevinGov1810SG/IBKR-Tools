"""
main.py — Point d'entrée et orchestration globale du système de trading IBKR.

Responsabilités :
  1. Initialiser tous les composants (broker, portfolio, strategies, agents, DB).
  2. Lancer la boucle principale (event loop) qui :
     - Récupère les données de marché
     - Exécute les agents d'analyse
     - Génère les signaux pour chaque stratégie
     - Vérifie le risque (RiskAgent) avant envoi d'ordre
     - Gère les exits via PositionTracker
     - Met à jour les métriques de performance
  3. Permettre un arrêt propre (Ctrl+C).
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Dict, List, Optional

from loguru import logger

# -- Configuration ----------------------------------------------------------
from config import (
    AppConfig, AssetConfig, MarketRegime,
    get_config, load_config,
)

# -- Database ---------------------------------------------------------------
from database.models import create_db_engine, create_session
from database.repository import (
    TradeRepository, SignalRepository, PerformanceRepository,
)

# -- Core -------------------------------------------------------------------
from core.broker import IBKRBroker
from core.data_feed import DataFeed
from core.portfolio import Portfolio
from core.risk_manager import RiskManager

# -- Strategies -------------------------------------------------------------
from strategies.base_strategy import BaseStrategy, TradeSignal
from strategies.momentum_strategy import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.breakout_strategy import BreakoutStrategy

# -- Agents -----------------------------------------------------------------
from agents.market_analyst import MarketAnalyst
from agents.risk_agent import RiskAgent
from agents.optimizer_agent import OptimizerAgent

# -- Execution --------------------------------------------------------------
from execution.order_manager import OrderManager
from execution.position_tracker import PositionTracker

# -- Analytics --------------------------------------------------------------
from analytics.performance import PerformanceCalculator
from analytics.reporter import Reporter


# ===========================================================================
# Orchestrator
# ===========================================================================

class TradingSystem:
    """Top-level orchestrator wiring every component together."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.cfg = config or get_config()
        self._running = False

        # -- Database ----------------------------------------------------------
        self._engine = create_db_engine(self.cfg.database_url)
        self._session = create_session(self._engine)
        self.trade_repo = TradeRepository(self._session)
        self.signal_repo = SignalRepository(self._session)
        self.perf_repo = PerformanceRepository(self._session)

        # -- Core --------------------------------------------------------------
        self.broker = IBKRBroker(self.cfg)
        self.data_feed = DataFeed(self.broker)
        self.portfolio = Portfolio()
        self.risk_manager = RiskManager(self.portfolio, self.data_feed)

        # -- Execution ---------------------------------------------------------
        self.order_manager = OrderManager(self.broker, self.trade_repo)
        self.position_tracker = PositionTracker(
            self.portfolio, self.data_feed, self.trade_repo,
        )

        # -- Strategies --------------------------------------------------------
        self.strategies: Dict[str, BaseStrategy] = {}
        if "momentum" in self.cfg.active_strategies:
            self.strategies["momentum"] = MomentumStrategy(self.cfg.momentum)
        if "mean_reversion" in self.cfg.active_strategies:
            self.strategies["mean_reversion"] = MeanReversionStrategy(self.cfg.mean_reversion)
        if "breakout" in self.cfg.active_strategies:
            self.strategies["breakout"] = BreakoutStrategy(self.cfg.breakout)

        # -- Agents ------------------------------------------------------------
        self.market_analyst = MarketAnalyst()
        self.risk_agent = RiskAgent(self.risk_manager)
        self.optimizer_agent = OptimizerAgent(self.trade_repo, self.cfg.optimizer)

        # -- Analytics ---------------------------------------------------------
        self.perf_calc = PerformanceCalculator(self.trade_repo, self.cfg.initial_capital)
        self.reporter = Reporter(self.perf_calc, self.trade_repo)

    # ======================================================================
    # Lifecycle
    # ======================================================================

    async def start(self) -> None:
        """Connect to IBKR, subscribe to data, and enter the main loop."""
        logger.info("=" * 60)
        logger.info("  IBKR Algorithmic Trading System — Starting")
        logger.info("=" * 60)

        # Connect broker
        await self.broker.connect()

        # Request portfolio updates
        self.broker.request_account_updates(callback=self.portfolio.on_portfolio_update)

        # Subscribe to real-time data for configured assets
        for asset in self.cfg.assets:
            self.data_feed.subscribe(asset)

        # Fetch initial historical data
        await self._fetch_all_historical()

        self._running = True
        logger.info("System initialised — entering main loop")

        try:
            await self._main_loop()
        except asyncio.CancelledError:
            logger.info("Main loop cancelled")
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        # Print final report
        logger.info("Generating final performance report…")
        self.reporter.print_report()
        # Disconnect
        self.broker.disconnect()
        self._session.close()
        logger.info("System stopped cleanly.")

    # ======================================================================
    # Main Loop
    # ======================================================================

    async def _main_loop(self) -> None:
        cycle = 0
        while self._running:
            cycle += 1
            logger.debug(f"--- Cycle {cycle} ---")

            try:
                # 1. Refresh historical data periodically (every 12 cycles)
                if cycle % 12 == 1:
                    await self._fetch_all_historical()

                # 2. Update position prices
                self.position_tracker.update_prices()

                # 3. Run Market Analyst agent
                market_data = {
                    a.symbol: self.data_feed.get_cached(a.symbol)
                    for a in self.cfg.assets
                    if self.data_feed.get_cached(a.symbol) is not None
                }
                await self.market_analyst.analyze({"market_data": market_data})
                regime_rec = self.market_analyst.get_recommendation()
                current_regime = regime_rec.details.get("regime", MarketRegime.RANGING)

                # 4. Check exits for open positions
                await self._handle_exits()

                # 5. Check risk halt
                if self.risk_manager.is_halted:
                    logger.warning("Risk manager halted — skipping signal generation")
                    await asyncio.sleep(self.cfg.main_loop_interval_sec)
                    continue

                # 6. Generate signals for each (strategy, asset) pair
                await self._generate_and_execute_signals(current_regime)

                # 7. Run optimizer agent periodically
                await self.optimizer_agent.analyze({
                    "strategies": self.strategies,
                })
                opt_rec = self.optimizer_agent.get_recommendation()
                if opt_rec.action == "adjust":
                    self._apply_param_adjustments(opt_rec.details.get("suggested_params", {}))

                # 8. Save performance snapshot periodically (every 20 cycles)
                if cycle % 20 == 0:
                    metrics = self.perf_calc.compute()
                    self.perf_repo.save_snapshot(
                        total_pnl=metrics.total_pnl,
                        daily_pnl=metrics.daily_pnl,
                        sharpe_ratio=metrics.sharpe_ratio,
                        max_drawdown=metrics.max_drawdown,
                        win_rate=metrics.win_rate,
                        total_trades=metrics.total_trades,
                        open_positions=self.portfolio.open_position_count,
                        capital=self.portfolio.total_equity,
                    )

            except Exception as exc:
                logger.exception(f"Error in cycle {cycle}: {exc}")

            await asyncio.sleep(self.cfg.main_loop_interval_sec)

    # ======================================================================
    # Signal generation & execution
    # ======================================================================

    async def _generate_and_execute_signals(self, regime: str) -> None:
        for strat_name, strategy in self.strategies.items():
            if not strategy.is_active:
                continue

            # Regime-based filtering
            if regime == MarketRegime.VOLATILE and strat_name == "mean_reversion":
                continue  # mean reversion doesn't work in volatile markets
            if regime == MarketRegime.RANGING and strat_name == "momentum":
                continue  # momentum needs trends

            for asset in self._get_assets_for_strategy(strat_name):
                # Skip if we already have a position via this strategy
                tracked = self.position_tracker.get_position(asset.symbol)
                if tracked and tracked.strategy_name == strat_name:
                    continue

                data = self.data_feed.get_cached(asset.symbol)
                if data is None or data.empty:
                    continue

                # Multi-timeframe for momentum
                multi_tf = None
                if strat_name == "momentum":
                    multi_tf = {}
                    for tf in self.cfg.momentum.timeframes:
                        key = f"{asset.symbol}_{tf}"
                        cached = self.data_feed.get_cached(key)
                        if cached is not None:
                            multi_tf[tf] = cached

                signal = strategy.generate_signal(asset.symbol, data, multi_tf=multi_tf)
                if signal is None:
                    continue

                # Compute position size
                price = self.data_feed.get_latest_price(asset.symbol)
                if price is None or price <= 0:
                    price = data["close"].iloc[-1]

                qty = strategy.compute_position_size(signal, data, self.portfolio.total_equity, price)
                if qty <= 0:
                    continue
                signal.suggested_quantity = qty

                # Save signal
                self.signal_repo.save_signal(
                    symbol=signal.symbol,
                    strategy=signal.strategy_name,
                    direction=signal.direction.value,
                    strength=signal.strength,
                )

                # Risk agent check
                await self.risk_agent.analyze({"signal": signal, "price": price})
                risk_rec = self.risk_agent.get_recommendation()

                if risk_rec.action != "approve":
                    self.signal_repo.save_signal(
                        symbol=signal.symbol,
                        strategy=signal.strategy_name,
                        direction=signal.direction.value,
                        strength=signal.strength,
                        approved=False,
                        reason=risk_rec.details.get("reason", ""),
                    )
                    continue

                adjusted_qty = risk_rec.details.get("adjusted_quantity", qty)

                # Execute!
                await self._execute_signal(asset, signal, price, adjusted_qty)

    async def _execute_signal(
        self,
        asset: AssetConfig,
        signal: TradeSignal,
        price: float,
        quantity: float,
    ) -> None:
        action = signal.direction.value  # "BUY" or "SELL"

        if signal.target_price and signal.stop_loss:
            # Bracket order
            self.order_manager.send_bracket_order(
                asset=asset,
                action=action,
                quantity=quantity,
                entry_price=price,
                take_profit=signal.target_price,
                stop_loss=signal.stop_loss,
                strategy=signal.strategy_name,
            )
        else:
            # Market order
            self.order_manager.send_market_order(
                asset=asset,
                action=action,
                quantity=quantity,
                strategy=signal.strategy_name,
            )

        # Track the position locally
        open_trades = self.trade_repo.get_open_trades(strategy=signal.strategy_name)
        trade_id = open_trades[-1].id if open_trades else 0

        self.position_tracker.register_position(
            trade_id=trade_id,
            symbol=asset.symbol,
            strategy_name=signal.strategy_name,
            direction=action,
            quantity=quantity,
            entry_price=price,
            target_price=signal.target_price,
            stop_loss=signal.stop_loss,
        )

        self.portfolio.add_position(asset.symbol, quantity, price, strategy=signal.strategy_name)

        if isinstance(self.strategies.get(signal.strategy_name), BreakoutStrategy):
            self.strategies[signal.strategy_name].register_entry(asset.symbol)

        logger.info(
            f"EXECUTED: {action} {quantity} {asset.symbol} @ ~{price:.2f} "
            f"strategy={signal.strategy_name}"
        )

    # ======================================================================
    # Exit handling
    # ======================================================================

    async def _handle_exits(self) -> None:
        positions_to_exit = self.position_tracker.check_exits(self.strategies)
        for pos in positions_to_exit:
            exit_price = self.data_feed.get_latest_price(pos.symbol)
            if exit_price is None or exit_price <= 0:
                cached = self.data_feed.get_cached(pos.symbol)
                if cached is not None and not cached.empty:
                    exit_price = cached["close"].iloc[-1]
                else:
                    continue

            # Send closing order
            reverse_action = "SELL" if pos.direction == "BUY" else "BUY"
            asset = self._find_asset(pos.symbol)
            if asset:
                self.order_manager.send_market_order(
                    asset=asset,
                    action=reverse_action,
                    quantity=pos.quantity,
                    strategy=pos.strategy_name,
                )

            self.position_tracker.close_position(pos.symbol, exit_price)

    # ======================================================================
    # Helpers
    # ======================================================================

    async def _fetch_all_historical(self) -> None:
        logger.info("Fetching historical data for all assets…")
        for asset in self.cfg.assets:
            try:
                await self.data_feed.fetch_historical(asset)
                await asyncio.sleep(0.5)  # IBKR rate limiting
            except Exception as e:
                logger.warning(f"Failed to fetch history for {asset.symbol}: {e}")

    def _get_assets_for_strategy(self, strat_name: str) -> List[AssetConfig]:
        """Return relevant assets depending on the strategy."""
        all_assets = self.cfg.assets
        if strat_name == "momentum":
            return [a for a in all_assets if a.sec_type in ("STK", "CASH", "FUT", "CRYPTO")]
        elif strat_name == "mean_reversion":
            return [a for a in all_assets if a.sec_type in ("CASH", "STK", "CRYPTO")]
        elif strat_name == "breakout":
            return [a for a in all_assets if a.sec_type in ("FUT", "STK", "CRYPTO")]
        return all_assets

    def _find_asset(self, symbol: str) -> Optional[AssetConfig]:
        for a in self.cfg.assets:
            if a.symbol == symbol:
                return a
        return None

    def _apply_param_adjustments(self, suggestions: Dict) -> None:
        """Apply optimizer suggestions to live strategies."""
        for strat_name, adjustments in suggestions.items():
            strat = self.strategies.get(strat_name)
            if strat is None or not adjustments:
                continue
            current = strat.params.model_dump() if hasattr(strat.params, "model_dump") else vars(strat.params)
            current.update(adjustments)
            new_params = type(strat.params)(**current)
            strat.update_params(new_params)
            logger.info(f"[Optimizer] Updated {strat_name} params: {adjustments}")


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    # Configure logging
    cfg = load_config()
    logger.remove()
    logger.add(
        sys.stderr,
        level=cfg.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{name}</cyan> - {message}",
    )
    logger.add(cfg.log_file, rotation="10 MB", level="DEBUG")

    system = TradingSystem(cfg)

    # Graceful shutdown on Ctrl+C
    loop = asyncio.new_event_loop()

    def _shutdown(sig, frame):
        logger.info(f"Received signal {sig} — shutting down…")
        system._running = False

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(system.start())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt — shutting down…")
        loop.run_until_complete(system.stop())
    finally:
        loop.close()


if __name__ == "__main__":
    main()

