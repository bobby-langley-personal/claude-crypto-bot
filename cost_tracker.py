from __future__ import annotations
"""
Cost tracking module for the crypto trading bot.

Estimates operational costs including:
- Claude API usage (sentiment analysis)
- News API calls (CryptoPanic, Reddit)
- AWS hosting costs (if deployed)
- Daily cost estimates with running averages

Tracks usage across bot cycles and provides breakdowns for the UI.
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


class CostTracker:
    """Tracks estimated operational costs for the trading bot."""
    
    def __init__(self, cost_file: str = "cost_data.json"):
        self.cost_file = Path(cost_file)
        self.data = self._load_data()
        
        # Pricing estimates (USD) - Current published rates as of Jan 2025
        self.prices = {
            "claude_haiku": {
                "input_tokens": 0.00000080,   # $0.80 per million input tokens
                "output_tokens": 0.00000400,  # $4.00 per million output tokens
                "cache_read_tokens": 0.00000008,  # $0.08 per million cache read tokens
            },
            "claude_sonnet": {
                "input_tokens": 0.00000300,   # $3.00 per million input tokens
                "output_tokens": 0.00001500,  # $15.00 per million output tokens  
                "cache_read_tokens": 0.00000030,  # $0.30 per million cache read tokens
            },
            "claude_opus": {
                "input_tokens": 0.00001500,   # $15.00 per million input tokens
                "output_tokens": 0.00007500,  # $75.00 per million output tokens
                "cache_read_tokens": 0.00000150,  # $1.50 per million cache read tokens
            },
            "cryptopanic_api": 0.00,          # Free tier
            "reddit_api": 0.00,               # Free
            "coingecko_api": 0.00,           # Free tier
            "aws_ec2_t2_micro": 0.0116,      # Per hour for t2.micro (free tier eligible)
            "aws_data_transfer": 0.09,        # Per GB after 1GB free
        }
    
    def _load_data(self) -> dict:
        """Load cost tracking data from file."""
        if not self.cost_file.exists():
            return {
                "total_cost": 0.0,
                "daily_costs": [],
                "service_totals": {},
                "claude_models": {},
                "last_reset": datetime.now().isoformat(),
                "session_start": datetime.now().isoformat(),
            }
        
        try:
            with open(self.cost_file) as f:
                return json.load(f)
        except Exception:
            return self._load_data.__defaults__[0]  # Return empty data if corrupted
    
    def _save_data(self) -> None:
        """Save cost tracking data to file."""
        try:
            with open(self.cost_file, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save cost data: {e}")
    
    def track_claude_usage(self, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, model: str = "claude-haiku") -> float:
        """Track Claude API usage and return cost for this call.
        
        Args:
            input_tokens: Number of input tokens consumed
            output_tokens: Number of output tokens generated
            cache_read_tokens: Number of cached tokens read (optional)
            model: Claude model used (claude-haiku, claude-sonnet, claude-opus)
        """
        # Normalize model name to match pricing keys
        model_key = model.replace("-4-5-20251001", "").replace("-20250514", "")
        if model_key not in self.prices:
            model_key = "claude_haiku"  # Default fallback
            
        pricing = self.prices[model_key]
        input_cost = input_tokens * pricing["input_tokens"]
        output_cost = output_tokens * pricing["output_tokens"]
        cache_cost = cache_read_tokens * pricing["cache_read_tokens"]
        total_cost = input_cost + output_cost + cache_cost
        
        timestamp = datetime.now().isoformat()
        
        # Track by specific model
        self._add_claude_model_cost(model_key, {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "input_cost": input_cost,
            "output_cost": output_cost,
            "cache_cost": cache_cost,
            "total_cost": total_cost,
            "timestamp": timestamp
        })
        
        # Also track in legacy claude_api service for backwards compatibility
        self._add_cost("claude_api", total_cost, {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "model": model,
            "timestamp": timestamp
        })
        
        return total_cost
    
    def _add_claude_model_cost(self, model: str, cost_data: dict) -> None:
        """Add cost tracking specifically for Claude models."""
        if "claude_models" not in self.data:
            self.data["claude_models"] = {}
            
        if model not in self.data["claude_models"]:
            self.data["claude_models"][model] = {
                "total_cost": 0.0,
                "total_calls": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_read_tokens": 0,
                "calls_history": []
            }
            
        model_data = self.data["claude_models"][model]
        model_data["total_cost"] += cost_data["total_cost"]
        model_data["total_calls"] += 1
        model_data["total_input_tokens"] += cost_data["input_tokens"]
        model_data["total_output_tokens"] += cost_data["output_tokens"]
        model_data["total_cache_read_tokens"] += cost_data["cache_read_tokens"]
        
        # Keep detailed call history (last 100 calls per model)
        model_data["calls_history"].append(cost_data)
        if len(model_data["calls_history"]) > 100:
            model_data["calls_history"] = model_data["calls_history"][-100:]
    
    def track_api_call(self, service: str, calls: int = 1) -> float:
        """Track API calls for news services (currently free)."""
        cost = calls * self.prices.get(f"{service}_api", 0.0)
        
        if cost > 0:
            self._add_cost(f"{service}_api", cost, {
                "calls": calls,
                "timestamp": datetime.now().isoformat()
            })
        
        return cost
    
    def track_aws_usage(self, hours: float = None) -> float:
        """Track estimated AWS costs (if deployed)."""
        if hours is None:
            # Estimate based on time since session start
            session_start = datetime.fromisoformat(self.data.get("session_start", datetime.now().isoformat()))
            hours = (datetime.now() - session_start).total_seconds() / 3600
        
        # t2.micro has 750 hours free per month
        monthly_free_hours = 750
        current_month_hours = self.data.get("service_totals", {}).get("aws_ec2", {}).get("monthly_hours", 0)
        
        billable_hours = max(0, current_month_hours + hours - monthly_free_hours)
        cost = billable_hours * self.prices["aws_ec2_t2_micro"]
        
        if cost > 0:
            self._add_cost("aws_ec2", cost, {
                "hours": hours,
                "billable_hours": billable_hours,
                "timestamp": datetime.now().isoformat()
            })
        
        return cost
    
    def _add_cost(self, service: str, cost: float, metadata: dict = None) -> None:
        """Add cost to tracking data."""
        # Add to total
        self.data["total_cost"] = self.data.get("total_cost", 0.0) + cost
        
        # Add to service totals
        if "service_totals" not in self.data:
            self.data["service_totals"] = {}
        if service not in self.data["service_totals"]:
            self.data["service_totals"][service] = {"total": 0.0, "calls": 0, "metadata": []}
        
        self.data["service_totals"][service]["total"] += cost
        self.data["service_totals"][service]["calls"] += 1
        if metadata:
            self.data["service_totals"][service]["metadata"].append(metadata)
            # Keep only last 100 entries per service
            if len(self.data["service_totals"][service]["metadata"]) > 100:
                self.data["service_totals"][service]["metadata"] = \
                    self.data["service_totals"][service]["metadata"][-100:]
        
        # Add to daily tracking
        today = datetime.now().date().isoformat()
        daily_costs = self.data.get("daily_costs", [])
        
        # Find or create today's entry
        today_entry = None
        for entry in daily_costs:
            if entry.get("date") == today:
                today_entry = entry
                break
        
        if today_entry is None:
            today_entry = {"date": today, "total": 0.0, "services": {}}
            daily_costs.append(today_entry)
        
        today_entry["total"] += cost
        if service not in today_entry["services"]:
            today_entry["services"][service] = 0.0
        today_entry["services"][service] += cost
        
        self.data["daily_costs"] = daily_costs
        
        # Keep only last 30 days
        if len(daily_costs) > 30:
            self.data["daily_costs"] = daily_costs[-30:]
        
        self._save_data()
    
    def get_current_total(self) -> float:
        """Get current total estimated cost."""
        return self.data.get("total_cost", 0.0)
    
    def get_daily_average(self, days: int = 7) -> float:
        """Get daily average cost over the last N days."""
        daily_costs = self.data.get("daily_costs", [])
        if not daily_costs:
            return 0.0
        
        recent_costs = daily_costs[-days:] if len(daily_costs) >= days else daily_costs
        if not recent_costs:
            return 0.0
        
        total = sum(entry.get("total", 0.0) for entry in recent_costs)
        return total / len(recent_costs)
    
    def get_cost_breakdown(self) -> dict:
        """Get detailed cost breakdown by service."""
        service_totals = self.data.get("service_totals", {})
        
        breakdown = {
            "total": self.get_current_total(),
            "daily_average": self.get_daily_average(),
            "services": {},
            "last_updated": datetime.now().isoformat(),
        }
        
        # Format service costs with friendly names
        service_names = {
            "claude_api": "Claude AI",
            "cryptopanic_api": "CryptoPanic News",
            "reddit_api": "Reddit News",
            "coingecko_api": "CoinGecko Data",
            "aws_ec2": "AWS Hosting",
        }
        
        for service, data in service_totals.items():
            friendly_name = service_names.get(service, service.title())
            breakdown["services"][friendly_name] = {
                "total": data.get("total", 0.0),
                "calls": data.get("calls", 0),
                "percentage": (data.get("total", 0.0) / max(breakdown["total"], 0.001)) * 100,
            }
        
        return breakdown
    
    def get_claude_model_breakdown(self) -> dict:
        """Get detailed breakdown of costs by Claude model."""
        claude_models = self.data.get("claude_models", {})
        
        breakdown = {
            "total_claude_cost": sum(model_data.get("total_cost", 0.0) for model_data in claude_models.values()),
            "models": {}
        }
        
        for model, data in claude_models.items():
            breakdown["models"][model] = {
                "calls": data.get("total_calls", 0),
                "input_tokens": data.get("total_input_tokens", 0),
                "output_tokens": data.get("total_output_tokens", 0),
                "cache_read_tokens": data.get("total_cache_read_tokens", 0),
                "cost": data.get("total_cost", 0.0)
            }
            
        return breakdown
    
    def get_cost_by_timeframe(self, timeframe: str) -> dict:
        """Get cost breakdown for specific timeframes: 'inception', '24h', '7d'."""
        now = datetime.now()
        
        if timeframe == "inception":
            return self.get_cost_breakdown()
        elif timeframe == "24h":
            cutoff = now - timedelta(hours=24)
        elif timeframe == "7d":
            cutoff = now - timedelta(days=7)
        else:
            return self.get_cost_breakdown()
            
        # Filter costs by timeframe
        total_cost = 0.0
        services = {}
        claude_models = {}
        
        # Filter service costs
        service_totals = self.data.get("service_totals", {})
        for service, data in service_totals.items():
            service_cost = 0.0
            service_calls = 0
            
            for metadata in data.get("metadata", []):
                try:
                    timestamp = datetime.fromisoformat(metadata.get("timestamp", ""))
                    if timestamp >= cutoff:
                        # Extract cost from metadata if available
                        if "total_cost" in metadata:
                            service_cost += metadata["total_cost"]
                        service_calls += 1
                except (ValueError, TypeError):
                    continue
                    
            if service_cost > 0 or service_calls > 0:
                services[service] = {
                    "total": service_cost,
                    "calls": service_calls
                }
                total_cost += service_cost
        
        # Filter Claude model costs
        claude_model_data = self.data.get("claude_models", {})
        for model, data in claude_model_data.items():
            model_cost = 0.0
            model_calls = 0
            input_tokens = 0
            output_tokens = 0
            
            for call_data in data.get("calls_history", []):
                try:
                    timestamp = datetime.fromisoformat(call_data.get("timestamp", ""))
                    if timestamp >= cutoff:
                        model_cost += call_data.get("total_cost", 0.0)
                        model_calls += 1
                        input_tokens += call_data.get("input_tokens", 0)
                        output_tokens += call_data.get("output_tokens", 0)
                except (ValueError, TypeError):
                    continue
                    
            if model_cost > 0:
                claude_models[model] = {
                    "calls": model_calls,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost": model_cost
                }
        
        return {
            "total": total_cost,
            "timeframe": timeframe,
            "services": services,
            "claude_models": claude_models,
            "last_updated": datetime.now().isoformat()
        }
    
    def reset_daily_costs(self) -> None:
        """Reset daily cost tracking (for new day)."""
        self.data["daily_costs"] = []
        self.data["last_reset"] = datetime.now().isoformat()
        self._save_data()
    
    def get_estimated_daily_cost(self) -> float:
        """Get estimated daily cost based on current usage patterns."""
        # If we have recent daily data, use the average
        if len(self.data.get("daily_costs", [])) > 0:
            return self.get_daily_average()
        
        # Otherwise, estimate based on session duration
        session_start = datetime.fromisoformat(self.data.get("session_start", datetime.now().isoformat()))
        hours_running = (datetime.now() - session_start).total_seconds() / 3600
        
        if hours_running < 0.1:  # Less than 6 minutes
            return 0.0
        
        # Extrapolate current costs to 24 hours
        current_cost = self.get_current_total()
        daily_estimate = (current_cost / hours_running) * 24
        
        return daily_estimate


# Global instance
cost_tracker = CostTracker()