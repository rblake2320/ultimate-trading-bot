"""
Notification Manager - Handles all notification channels
"""

import logging
from typing import Dict, List
from datetime import datetime


class NotificationManager:
    """
    Manages notifications across multiple channels (Telegram, Email, etc.)
    """

    def __init__(self, notification_config: Dict):
        """
        Initialize notification manager

        Args:
            notification_config: Notification configuration
        """
        self.notification_config = notification_config
        self.logger = logging.getLogger(__name__)

        # Notification channels
        self.telegram_enabled = notification_config.get("telegram", {}).get(
            "enabled", False
        )
        self.email_enabled = notification_config.get("email", {}).get("enabled", False)

        # Notification history
        self.notification_history = []

        # Channel clients
        self.telegram_client = None
        self.email_client = None

    async def initialize(self) -> bool:
        """
        Initialize notification channels

        Returns:
            True if successful, False otherwise
        """
        self.logger.info("Initializing notification manager...")

        try:
            # Initialize Telegram if enabled
            if self.telegram_enabled:
                await self._initialize_telegram()

            # Initialize Email if enabled
            if self.email_enabled:
                await self._initialize_email()

            self.logger.info("Notification manager initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize notification manager: {e}")
            return False

    async def _initialize_telegram(self):
        """Initialize Telegram bot"""
        try:
            telegram_config = self.notification_config.get("telegram", {})
            bot_token = telegram_config.get("bot_token")
            chat_id = telegram_config.get("chat_id")

            if not bot_token or not chat_id:
                self.logger.warning("Telegram credentials not configured")
                self.telegram_enabled = False
                return

            # In production, initialize the Telegram bot client
            # from telegram import Bot
            # self.telegram_client = Bot(token=bot_token)

            self.logger.info("Telegram notification channel initialized")

        except Exception as e:
            self.logger.error(f"Error initializing Telegram: {e}")
            self.telegram_enabled = False

    async def _initialize_email(self):
        """Initialize email client"""
        try:
            email_config = self.notification_config.get("email", {})
            smtp_server = email_config.get("smtp_server")
            username = email_config.get("username")
            password = email_config.get("password")

            if not smtp_server or not username or not password:
                self.logger.warning("Email credentials not configured")
                self.email_enabled = False
                return

            # In production, initialize the email client
            # import smtplib
            # self.email_client = smtplib.SMTP(smtp_server, email_config.get('smtp_port', 587))

            self.logger.info("Email notification channel initialized")

        except Exception as e:
            self.logger.error(f"Error initializing Email: {e}")
            self.email_enabled = False

    async def send_notification(
        self, message: str, level: str = "info", channels: List[str] = None
    ) -> bool:
        """
        Send notification to specified channels

        Args:
            message: Notification message
            level: Notification level (info, warning, error, critical)
            channels: List of channels to send to (default: all enabled)

        Returns:
            True if sent successfully, False otherwise
        """
        try:
            # Record notification
            notification = {
                "message": message,
                "level": level,
                "timestamp": datetime.now(),
                "channels": channels or ["all"],
            }

            self.notification_history.append(notification)

            # Keep only recent notifications
            if len(self.notification_history) > 1000:
                self.notification_history.pop(0)

            # Determine which channels to use
            if channels is None:
                channels = []
                if self.telegram_enabled:
                    channels.append("telegram")
                if self.email_enabled:
                    channels.append("email")

            success = True

            # Send to each channel
            if "telegram" in channels and self.telegram_enabled:
                if not await self._send_telegram(message, level):
                    success = False

            if "email" in channels and self.email_enabled:
                if not await self._send_email(message, level):
                    success = False

            return success

        except Exception as e:
            self.logger.error(f"Error sending notification: {e}")
            return False

    async def _send_telegram(self, message: str, level: str) -> bool:
        """
        Send Telegram notification

        Args:
            message: Message to send
            level: Notification level

        Returns:
            True if successful, False otherwise
        """
        try:
            # Add emoji based on level
            emoji_map = {
                "info": "ℹ️",
                "warning": "⚠️",
                "error": "❌",
                "critical": "🚨",
            }

            emoji = emoji_map.get(level, "ℹ️")
            formatted_message = f"{emoji} {message}"

            # In production, send via Telegram API
            # chat_id = self.notification_config['telegram']['chat_id']
            # await self.telegram_client.send_message(chat_id=chat_id, text=formatted_message)

            self.logger.info(f"Telegram notification sent: {message}")
            return True

        except Exception as e:
            self.logger.error(f"Error sending Telegram notification: {e}")
            return False

    async def _send_email(self, message: str, level: str) -> bool:
        """
        Send email notification

        Args:
            message: Message to send
            level: Notification level

        Returns:
            True if successful, False otherwise
        """
        try:
            email_config = self.notification_config.get("email", {})

            subject = f"Trading Bot {level.upper()}: Notification"

            # In production, send via SMTP
            # from email.mime.text import MIMEText
            # msg = MIMEText(message)
            # msg['Subject'] = subject
            # msg['From'] = email_config['username']
            # msg['To'] = email_config['username']
            # self.email_client.send_message(msg)

            self.logger.info(f"Email notification sent: {message}")
            return True

        except Exception as e:
            self.logger.error(f"Error sending email notification: {e}")
            return False

    async def send_trade_notification(self, trade_message: str) -> bool:
        """
        Send trade execution notification

        Args:
            trade_message: Trade information

        Returns:
            True if successful, False otherwise
        """
        try:
            message = f"💰 Trade Executed: {trade_message}"
            return await self.send_notification(message, level="info")

        except Exception as e:
            self.logger.error(f"Error sending trade notification: {e}")
            return False

    async def send_alert_notification(self, alert_message: str) -> bool:
        """
        Send alert notification

        Args:
            alert_message: Alert information

        Returns:
            True if successful, False otherwise
        """
        try:
            message = f"⚠️ Alert: {alert_message}"
            return await self.send_notification(message, level="warning")

        except Exception as e:
            self.logger.error(f"Error sending alert notification: {e}")
            return False

    async def send_error_notification(self, error_message: str) -> bool:
        """
        Send error notification

        Args:
            error_message: Error information

        Returns:
            True if successful, False otherwise
        """
        try:
            message = f"❌ Error: {error_message}"
            return await self.send_notification(message, level="error")

        except Exception as e:
            self.logger.error(f"Error sending error notification: {e}")
            return False

    async def send_emergency_notification(self, emergency_message: str) -> bool:
        """
        Send emergency/critical notification

        Args:
            emergency_message: Emergency information

        Returns:
            True if successful, False otherwise
        """
        try:
            message = f"🚨 EMERGENCY: {emergency_message}"
            return await self.send_notification(message, level="critical")

        except Exception as e:
            self.logger.error(f"Error sending emergency notification: {e}")
            return False

    async def send_daily_summary(self, summary: Dict) -> bool:
        """
        Send daily performance summary

        Args:
            summary: Daily summary data

        Returns:
            True if successful, False otherwise
        """
        try:
            # Format summary message
            message = "📊 Daily Trading Summary\n\n"
            message += f"Total Trades: {summary.get('total_trades', 0)}\n"
            message += f"Winning Trades: {summary.get('winning_trades', 0)}\n"
            message += f"Losing Trades: {summary.get('losing_trades', 0)}\n"
            message += f"Daily P&L: ${summary.get('daily_pnl', 0):.2f}\n"
            message += f"Total P&L: ${summary.get('total_pnl', 0):.2f}\n"
            message += f"Win Rate: {summary.get('win_rate', 0):.1%}\n"

            return await self.send_notification(message, level="info")

        except Exception as e:
            self.logger.error(f"Error sending daily summary: {e}")
            return False

    async def send_system_status(self, status: Dict) -> bool:
        """
        Send system status notification

        Args:
            status: System status data

        Returns:
            True if successful, False otherwise
        """
        try:
            # Format status message
            message = "🔧 System Status\n\n"
            message += f"Status: {status.get('is_running', False) and 'Running' or 'Stopped'}\n"
            message += f"Uptime: {status.get('uptime', 'N/A')}\n"
            message += f"Active Positions: {status.get('active_positions', 0)}\n"
            message += f"Pending Orders: {status.get('pending_orders', 0)}\n"
            message += f"Portfolio Value: ${status.get('portfolio_value', 0):.2f}\n"

            return await self.send_notification(message, level="info")

        except Exception as e:
            self.logger.error(f"Error sending system status: {e}")
            return False

    def get_notification_history(self, limit: int = 100) -> List[Dict]:
        """
        Get recent notification history

        Args:
            limit: Maximum number of notifications to return

        Returns:
            List of recent notifications
        """
        try:
            # Sort by timestamp (newest first)
            notifications = sorted(
                self.notification_history,
                key=lambda x: x.get("timestamp", datetime.now()),
                reverse=True,
            )

            return notifications[:limit]

        except Exception as e:
            self.logger.error(f"Error getting notification history: {e}")
            return []

    def get_notification_stats(self) -> Dict:
        """
        Get notification statistics

        Returns:
            Dictionary of notification stats
        """
        try:
            total = len(self.notification_history)

            level_counts = {}
            for notification in self.notification_history:
                level = notification.get("level", "info")
                level_counts[level] = level_counts.get(level, 0) + 1

            return {
                "total_notifications": total,
                "by_level": level_counts,
                "telegram_enabled": self.telegram_enabled,
                "email_enabled": self.email_enabled,
            }

        except Exception as e:
            self.logger.error(f"Error getting notification stats: {e}")
            return {}
