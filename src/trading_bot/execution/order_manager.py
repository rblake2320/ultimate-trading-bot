"""
Order Manager - Handles order execution and management
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import uuid

class OrderManager:
    """
    Manages order execution, tracking, and lifecycle
    """
    
    def __init__(self, trading_config: Dict):
        """
        Initialize order manager
        
        Args:
            trading_config: Trading configuration
        """
        self.trading_config = trading_config
        self.logger = logging.getLogger(__name__)
        self.active_orders = {}
        self.order_history = []
        
    async def place_buy_order(self, symbol: str, size: float, price: float = None, 
                             order_type: str = 'limit', exchange_name: str = None) -> Optional[Dict]:
        """
        Place a buy order
        
        Args:
            symbol: Trading symbol
            size: Order size
            price: Order price (for limit orders)
            order_type: 'market' or 'limit'
            exchange_name: Specific exchange
            
        Returns:
            Order data or None
        """
        try:
            order_id = str(uuid.uuid4())
            
            order = {
                'id': order_id,
                'symbol': symbol,
                'side': 'buy',
                'type': order_type,
                'size': size,
                'price': price,
                'status': 'pending',
                'timestamp': datetime.now(),
                'exchange': exchange_name
            }
            
            # Validate order
            if not await self._validate_order(order):
                return None
            
            # Execute order through exchange manager
            # This would integrate with the exchange manager
            executed_order = await self._execute_order(order)
            
            if executed_order:
                self.active_orders[order_id] = executed_order
                self.order_history.append(executed_order)
                
                self.logger.info(f"Buy order placed: {order_id} {symbol} {size}")
                return executed_order
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error placing buy order: {e}")
            return None
    
    async def place_sell_order(self, symbol: str, size: float, price: float = None, 
                              order_type: str = 'limit', exchange_name: str = None) -> Optional[Dict]:
        """
        Place a sell order
        
        Args:
            symbol: Trading symbol
            size: Order size
            price: Order price (for limit orders)
            order_type: 'market' or 'limit'
            exchange_name: Specific exchange
            
        Returns:
            Order data or None
        """
        try:
            order_id = str(uuid.uuid4())
            
            order = {
                'id': order_id,
                'symbol': symbol,
                'side': 'sell',
                'type': order_type,
                'size': size,
                'price': price,
                'status': 'pending',
                'timestamp': datetime.now(),
                'exchange': exchange_name
            }
            
            # Validate order
            if not await self._validate_order(order):
                return None
            
            # Execute order through exchange manager
            executed_order = await self._execute_order(order)
            
            if executed_order:
                self.active_orders[order_id] = executed_order
                self.order_history.append(executed_order)
                
                self.logger.info(f"Sell order placed: {order_id} {symbol} {size}")
                return executed_order
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error placing sell order: {e}")
            return None
    
    async def _validate_order(self, order: Dict) -> bool:
        """
        Validate order parameters
        
        Args:
            order: Order data
            
        Returns:
            True if valid, False otherwise
        """
        try:
            # Check minimum order size
            if order['size'] <= 0:
                self.logger.error("Order size must be positive")
                return False
            
            # Check maximum position size
            max_position = self.trading_config.get('max_position_size', 0.1)
            if order['size'] > max_position:
                self.logger.error(f"Order size exceeds maximum: {max_position}")
                return False
            
            # Check price for limit orders
            if order['type'] == 'limit' and order['price'] <= 0:
                self.logger.error("Limit order price must be positive")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error validating order: {e}")
            return False
    
    async def _execute_order(self, order: Dict) -> Optional[Dict]:
        """
        Execute order through exchange
        
        Args:
            order: Order data
            
        Returns:
            Executed order data or None
        """
        try:
            # This would integrate with the exchange manager
            # For now, simulate order execution
            
            order['status'] = 'submitted'
            order['exchange_order_id'] = f"exchange_{order['id']}"
            
            return order
            
        except Exception as e:
            self.logger.error(f"Error executing order: {e}")
            return None
    
    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an active order
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if order_id not in self.active_orders:
                self.logger.error(f"Order not found: {order_id}")
                return False
            
            order = self.active_orders[order_id]
            
            # Cancel through exchange
            # This would integrate with exchange manager
            
            order['status'] = 'cancelled'
            order['cancelled_at'] = datetime.now()
            
            del self.active_orders[order_id]
            
            self.logger.info(f"Order cancelled: {order_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error cancelling order {order_id}: {e}")
            return False
    
    async def get_order_status(self, order_id: str) -> Optional[Dict]:
        """
        Get order status
        
        Args:
            order_id: Order ID
            
        Returns:
            Order status data or None
        """
        try:
            if order_id in self.active_orders:
                order = self.active_orders[order_id]
                
                # Check status with exchange
                # This would integrate with exchange manager
                
                return {
                    'id': order_id,
                    'status': order['status'],
                    'filled_size': order.get('filled_size', 0),
                    'remaining_size': order['size'] - order.get('filled_size', 0),
                    'average_price': order.get('average_price', order.get('price')),
                    'fees': order.get('fees', 0)
                }
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting order status {order_id}: {e}")
            return None
    
    async def update_order_status(self, order_id: str, status_data: Dict):
        """
        Update order status from exchange
        
        Args:
            order_id: Order ID
            status_data: Status update data
        """
        try:
            if order_id in self.active_orders:
                order = self.active_orders[order_id]
                order.update(status_data)
                
                # If order is filled or cancelled, remove from active orders
                if status_data.get('status') in ['filled', 'cancelled']:
                    del self.active_orders[order_id]
                    
        except Exception as e:
            self.logger.error(f"Error updating order status: {e}")
    
    async def get_active_orders(self) -> List[Dict]:
        """
        Get all active orders
        
        Returns:
            List of active orders
        """
        return list(self.active_orders.values())
    
    async def get_order_history(self, symbol: str = None, limit: int = 100) -> List[Dict]:
        """
        Get order history
        
        Args:
            symbol: Filter by symbol (optional)
            limit: Maximum number of orders to return
            
        Returns:
            List of historical orders
        """
        try:
            orders = self.order_history
            
            if symbol:
                orders = [order for order in orders if order['symbol'] == symbol]
            
            # Sort by timestamp (newest first)
            orders = sorted(orders, key=lambda x: x['timestamp'], reverse=True)
            
            return orders[:limit]
            
        except Exception as e:
            self.logger.error(f"Error getting order history: {e}")
            return []
    
    async def cancel_all_orders(self, symbol: str = None) -> int:
        """
        Cancel all active orders
        
        Args:
            symbol: Cancel orders for specific symbol (optional)
            
        Returns:
            Number of orders cancelled
        """
        try:
            orders_to_cancel = []
            
            for order_id, order in self.active_orders.items():
                if symbol is None or order['symbol'] == symbol:
                    orders_to_cancel.append(order_id)
            
            cancelled_count = 0
            for order_id in orders_to_cancel:
                if await self.cancel_order(order_id):
                    cancelled_count += 1
            
            self.logger.info(f"Cancelled {cancelled_count} orders")
            return cancelled_count
            
        except Exception as e:
            self.logger.error(f"Error cancelling all orders: {e}")
            return 0
    
    def get_order_statistics(self) -> Dict:
        """
        Get order execution statistics
        
        Returns:
            Order statistics
        """
        try:
            total_orders = len(self.order_history)
            filled_orders = len([o for o in self.order_history if o.get('status') == 'filled'])
            cancelled_orders = len([o for o in self.order_history if o.get('status') == 'cancelled'])
            
            fill_rate = filled_orders / total_orders if total_orders > 0 else 0
            
            return {
                'total_orders': total_orders,
                'filled_orders': filled_orders,
                'cancelled_orders': cancelled_orders,
                'fill_rate': fill_rate,
                'active_orders': len(self.active_orders)
            }
            
        except Exception as e:
            self.logger.error(f"Error calculating order statistics: {e}")
            return {}

