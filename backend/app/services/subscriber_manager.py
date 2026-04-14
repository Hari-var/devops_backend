"""Thread-safe subscriber management for SSE connections."""
import asyncio
import threading
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class SubscriberManager:
    """Thread-safe manager for SSE subscribers."""
    
    def __init__(self):
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._lock = threading.RLock()  # Reentrant lock for nested calls
    
    def add_subscriber(self, approval_id: str, queue: asyncio.Queue) -> None:
        """Add a subscriber queue for an approval ID."""
        with self._lock:
            if approval_id not in self._subscribers:
                self._subscribers[approval_id] = []
            self._subscribers[approval_id].append(queue)
            logger.debug(f"Added subscriber for {approval_id}, total: {len(self._subscribers[approval_id])}")
    
    def remove_subscriber(self, approval_id: str, queue: asyncio.Queue) -> None:
        """Remove a specific subscriber queue."""
        with self._lock:
            if approval_id in self._subscribers:
                try:
                    self._subscribers[approval_id].remove(queue)
                    logger.debug(f"Removed subscriber for {approval_id}")
                    
                    # Clean up empty lists
                    if not self._subscribers[approval_id]:
                        del self._subscribers[approval_id]
                        logger.debug(f"Cleaned up empty subscriber list for {approval_id}")
                except ValueError:
                    # Queue not in list, ignore
                    pass
    
    def get_subscribers(self, approval_id: str) -> List[asyncio.Queue]:
        """Get all subscriber queues for an approval ID."""
        with self._lock:
            return self._subscribers.get(approval_id, []).copy()  # Return copy to avoid modification during iteration
    
    def broadcast_message(self, approval_id: str, message: str) -> int:
        """Broadcast a message to all subscribers of an approval ID."""
        subscribers = self.get_subscribers(approval_id)
        sent_count = 0
        
        for queue in subscribers:
            try:
                queue.put_nowait(message)
                sent_count += 1
            except asyncio.QueueFull:
                logger.warning(f"Queue full for subscriber of {approval_id}")
            except Exception as e:
                logger.warning(f"Failed to send message to subscriber of {approval_id}: {e}")
        
        return sent_count
    
    def cleanup_approval(self, approval_id: str) -> int:
        """Clean up all subscribers for an approval ID."""
        with self._lock:
            if approval_id not in self._subscribers:
                return 0
            
            subscribers = self._subscribers[approval_id].copy()
            cleanup_count = 0
            
            # Send cleanup message to all subscribers
            for queue in subscribers:
                try:
                    queue.put_nowait("CLEANUP")
                    cleanup_count += 1
                except:
                    pass
            
            # Remove the approval ID
            try:
                del self._subscribers[approval_id]
                logger.info(f"Cleaned up {cleanup_count} subscribers for {approval_id}")
            except KeyError:
                pass  # Already cleaned up
            
            return cleanup_count
    
    def get_stats(self) -> Dict[str, int]:
        """Get subscriber statistics."""
        with self._lock:
            return {
                "total_approvals": len(self._subscribers),
                "total_subscribers": sum(len(queues) for queues in self._subscribers.values())
            }


# Global subscriber manager instance
subscriber_manager = SubscriberManager()