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
        
        # Pricing estimates (USD)
        self.prices = {
            "claude_api": {
                "input_tokens": 0.00000300,   # $3 per million input tokens (Claude Haiku)
                "output_tokens": 0.00001500,  # $15 per million output tokens
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
    
    def track_claude_usage(self, input_tokens: int, output_tokens: int) -> float:
        """Track Claude API usage and return cost for this call."""
        input_cost = input_tokens * self.prices["claude_api"]["input_tokens"]
        output_cost = output_tokens * self.prices["claude_api"]["output_tokens"]
        total_cost = input_cost + output_cost
        
        self._add_cost("claude_api", total_cost, {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "timestamp": datetime.now().isoformat()
        })
        
        return total_cost
    
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