"""
Industry-grade communication platform (Bloomberg Chat equivalent).
Secure, verified communication for music industry professionals.
"""
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import uuid
import json


class VerificationStatus(Enum):
    """User verification status."""
    UNVERIFIED = "unverified"
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class MessageType(Enum):
    """Message types."""
    DIRECT = "direct"
    GROUP = "group"
    BROADCAST = "broadcast"
    SYSTEM = "system"


class MessagePriority(Enum):
    """Message priority levels."""
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class User:
    """Verified user in the communication platform."""
    user_id: str
    name: str
    email: str
    company: Optional[str] = None
    role: Optional[str] = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    verification_date: Optional[datetime] = None
    profile: Optional[Dict[str, Any]] = None


@dataclass
class Message:
    """Message in the communication platform."""
    message_id: str
    sender_id: str
    content: str
    message_type: MessageType
    recipient_id: Optional[str] = None  # None for group/broadcast
    group_id: Optional[str] = None
    priority: MessagePriority = MessagePriority.NORMAL
    created_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class GroupChat:
    """Group chat in the communication platform."""
    group_id: str
    name: str
    creator_id: str
    participants: List[str]
    topic: Optional[str] = None
    created_at: datetime = None
    is_private: bool = True


class IndustryCommunicationPlatform:
    """Bloomberg-style communication platform."""
    
    def __init__(self):
        self.user_directory = VerifiedUserDirectory()
        self.messaging_system = SecureMessagingSystem()
        self.group_chat = GroupChatSystem()
        self.broadcast_system = BroadcastSystem()
        self.file_sharing = SecureFileSharing()
        self.verification_system = IdentityVerificationSystem()
        self.notification_system = NotificationSystem()
    
    def verify_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Verify user identity (industry-standard verification).
        
        Args:
            user_data: User data for verification
            
        Returns:
            Verification result with user ID if successful
        """
        verification_methods = [
            'email_verification',
            'phone_verification',
            'professional_verification',
            'company_verification'
        ]
        
        verification_results = []
        for method in verification_methods:
            result = self.verification_system.verify(user_data, method)
            verification_results.append(result)
        
        # Require at least 3 verification methods to pass
        passed_count = sum(1 for result in verification_results if result['verified'])
        
        if passed_count >= 3:
            user_id = self.user_directory.add_verified_user(user_data)
            return {
                'success': True,
                'user_id': user_id,
                'verification_status': VerificationStatus.VERIFIED.value,
                'methods_passed': passed_count
            }
        else:
            return {
                'success': False,
                'verification_status': VerificationStatus.PENDING.value,
                'methods_passed': passed_count,
                'methods_required': 3,
                'errors': [result['error'] for result in verification_results if not result['verified']]
            }
    
    def send_direct_message(self, sender_id: str, recipient_id: str, content: str, 
                           priority: MessagePriority = MessagePriority.NORMAL) -> Dict[str, Any]:
        """
        Send secure direct message.
        
        Args:
            sender_id: Sender user ID
            recipient_id: Recipient user ID
            content: Message content
            priority: Message priority
            
        Returns:
            Message send result
        """
        # Verify sender
        if not self.user_directory.is_verified(sender_id):
            return {'success': False, 'error': 'Sender not verified'}
        
        # Verify recipient
        if not self.user_directory.is_verified(recipient_id):
            return {'success': False, 'error': 'Recipient not verified'}
        
        # Create message
        message = Message(
            message_id=str(uuid.uuid4()),
            sender_id=sender_id,
            recipient_id=recipient_id,
            content=content,
            message_type=MessageType.DIRECT,
            priority=priority,
            created_at=datetime.utcnow()
        )
        
        # Send message
        message_id = self.messaging_system.send(message)
        
        # Notify recipient
        self.notification_system.notify_user(recipient_id, {
            'type': 'new_message',
            'message_id': message_id,
            'sender_id': sender_id,
            'priority': priority.value
        })
        
        return {
            'success': True,
            'message_id': message_id,
            'sent_at': message.created_at.isoformat() if message.created_at else None
        }
    
    def create_group_chat(self, creator_id: str, participants: List[str], 
                         topic: str, name: str, is_private: bool = True) -> Dict[str, Any]:
        """
        Create industry group chat.
        
        Args:
            creator_id: Creator user ID
            participants: List of participant user IDs
            topic: Group topic
            name: Group name
            is_private: Whether group is private
            
        Returns:
            Group creation result
        """
        # Verify creator
        if not self.user_directory.is_verified(creator_id):
            return {'success': False, 'error': 'Creator not verified'}
        
        # Verify all participants
        for participant in participants:
            if not self.user_directory.is_verified(participant):
                return {'success': False, 'error': f'Participant {participant} not verified'}
        
        # Create group
        group = GroupChat(
            group_id=str(uuid.uuid4()),
            name=name,
            creator_id=creator_id,
            participants=[creator_id] + participants,
            topic=topic,
            created_at=datetime.utcnow(),
            is_private=is_private
        )
        
        # Save group
        group_id = self.group_chat.create(group)
        
        # Notify participants
        for participant in group.participants:
            if participant != creator_id:
                self.notification_system.notify_user(participant, {
                    'type': 'group_invitation',
                    'group_id': group_id,
                    'group_name': name,
                    'inviter_id': creator_id
                })
        
        return {
            'success': True,
            'group_id': group_id,
            'participants': group.participants,
            'created_at': group.created_at.isoformat()
        }
    
    def send_group_message(self, sender_id: str, group_id: str, content: str,
                          priority: MessagePriority = MessagePriority.NORMAL) -> Dict[str, Any]:
        """
        Send message to group chat.
        
        Args:
            sender_id: Sender user ID
            group_id: Group ID
            content: Message content
            priority: Message priority
            
        Returns:
            Message send result
        """
        # Verify sender
        if not self.user_directory.is_verified(sender_id):
            return {'success': False, 'error': 'Sender not verified'}
        
        # Verify group exists and sender is member
        group = self.group_chat.get_group(group_id)
        if not group:
            return {'success': False, 'error': 'Group not found'}
        
        if sender_id not in group.participants:
            return {'success': False, 'error': 'Sender not in group'}
        
        # Create message
        message = Message(
            message_id=str(uuid.uuid4()),
            sender_id=sender_id,
            group_id=group_id,
            content=content,
            message_type=MessageType.GROUP,
            priority=priority,
            created_at=datetime.utcnow()
        )
        
        # Send message
        message_id = self.messaging_system.send_to_group(message)
        
        # Notify all participants
        for participant in group.participants:
            if participant != sender_id:
                self.notification_system.notify_user(participant, {
                    'type': 'group_message',
                    'message_id': message_id,
                    'group_id': group_id,
                    'sender_id': sender_id,
                    'priority': priority.value
                })
        
        return {
            'success': True,
            'message_id': message_id,
            'sent_at': message.created_at.isoformat() if message.created_at else None
        }
    
    def industry_broadcast(self, sender_id: str, target_criteria: Dict[str, Any], 
                          content: str, priority: MessagePriority = MessagePriority.HIGH) -> Dict[str, Any]:
        """
        Broadcast to industry professionals meeting criteria.
        
        Args:
            sender_id: Sender user ID
            target_criteria: Criteria for targeting recipients
            content: Message content
            priority: Message priority
            
        Returns:
            Broadcast result
        """
        # Verify sender
        if not self.user_directory.is_verified(sender_id):
            return {'success': False, 'error': 'Sender not verified'}
        
        # Find targets matching criteria
        targets = self.user_directory.find_by_criteria(target_criteria)
        
        # Create broadcast message
        message = Message(
            message_id=str(uuid.uuid4()),
            sender_id=sender_id,
            content=content,
            message_type=MessageType.BROADCAST,
            priority=priority,
            created_at=datetime.utcnow(),
            metadata={'target_criteria': target_criteria, 'target_count': len(targets)}
        )
        
        # Send broadcast
        results = self.broadcast_system.send(message, targets)
        
        # Notify recipients
        for target in targets:
            self.notification_system.notify_user(target['user_id'], {
                'type': 'broadcast',
                'message_id': message.message_id,
                'sender_id': sender_id,
                'priority': priority.value
            })
        
        return {
            'success': True,
            'message_id': message.message_id,
            'reached': len(results),
            'targets': targets
        }
    
    def share_file(self, sender_id: str, recipient_id: Optional[str], group_id: Optional[str],
                  file_data: Dict[str, Any], access_level: str = 'restricted') -> Dict[str, Any]:
        """
        Share file securely.
        
        Args:
            sender_id: Sender user ID
            recipient_id: Recipient user ID (for direct share)
            group_id: Group ID (for group share)
            file_data: File information
            access_level: Access level (public, restricted, confidential)
            
        Returns:
            File share result
        """
        # Verify sender
        if not self.user_directory.is_verified(sender_id):
            return {'success': False, 'error': 'Sender not verified'}
        
        # Verify recipient or group
        if recipient_id and not self.user_directory.is_verified(recipient_id):
            return {'success': False, 'error': 'Recipient not verified'}
        
        if group_id:
            group = self.group_chat.get_group(group_id)
            if not group or sender_id not in group.participants:
                return {'success': False, 'error': 'Invalid group or access'}
        
        # Share file
        file_id = self.file_sharing.share(sender_id, recipient_id, group_id, file_data, access_level)
        
        # Notify recipient(s)
        if recipient_id:
            self.notification_system.notify_user(recipient_id, {
                'type': 'file_shared',
                'file_id': file_id,
                'sender_id': sender_id,
                'access_level': access_level
            })
        elif group_id:
            group = self.group_chat.get_group(group_id)
            for participant in group.participants:
                if participant != sender_id:
                    self.notification_system.notify_user(participant, {
                        'type': 'file_shared',
                        'file_id': file_id,
                        'sender_id': sender_id,
                        'group_id': group_id,
                        'access_level': access_level
                    })
        
        return {
            'success': True,
            'file_id': file_id,
            'shared_at': datetime.utcnow().isoformat()
        }
    
    def get_conversation_history(self, user_id: str, other_user_id: Optional[str] = None,
                                group_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get conversation history.
        
        Args:
            user_id: User ID requesting history
            other_user_id: Other user ID (for direct conversation)
            group_id: Group ID (for group conversation)
            limit: Maximum number of messages to return
            
        Returns:
            Conversation history
        """
        # Verify user
        if not self.user_directory.is_verified(user_id):
            return []
        
        if other_user_id:
            # Direct conversation
            return self.messaging_system.get_direct_conversation(user_id, other_user_id, limit)
        elif group_id:
            # Group conversation
            group = self.group_chat.get_group(group_id)
            if group and user_id in group.participants:
                return self.messaging_system.get_group_conversation(group_id, limit)
            else:
                return []
        else:
            return []


class VerifiedUserDirectory:
    """Directory of verified users."""
    
    def __init__(self):
        self.users = {}  # user_id -> User
        self.email_index = {}  # email -> user_id
        self.company_index = {}  # company -> [user_ids]
    
    def add_verified_user(self, user_data: Dict[str, Any]) -> str:
        """Add verified user to directory."""
        user_id = str(uuid.uuid4())
        
        user = User(
            user_id=user_id,
            name=user_data.get('name', ''),
            email=user_data.get('email', ''),
            company=user_data.get('company'),
            role=user_data.get('role'),
            verification_status=VerificationStatus.VERIFIED,
            verification_date=datetime.utcnow(),
            profile=user_data.get('profile', {})
        )
        
        self.users[user_id] = user
        self.email_index[user.email] = user_id
        
        if user.company:
            if user.company not in self.company_index:
                self.company_index[user.company] = []
            self.company_index[user.company].append(user_id)
        
        return user_id
    
    def is_verified(self, user_id: str) -> bool:
        """Check if user is verified."""
        user = self.users.get(user_id)
        return user is not None and user.verification_status == VerificationStatus.VERIFIED
    
    def find_by_criteria(self, criteria: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Find users matching criteria."""
        results = []
        
        for user_id, user in self.users.items():
            match = True
            
            if 'company' in criteria and user.company != criteria['company']:
                match = False
            
            if 'role' in criteria and user.role != criteria['role']:
                match = False
            
            if 'region' in criteria:
                user_region = user.profile.get('region') if user.profile else None
                if user_region != criteria['region']:
                    match = False
            
            if match:
                results.append({
                    'user_id': user_id,
                    'name': user.name,
                    'email': user.email,
                    'company': user.company,
                    'role': user.role
                })
        
        return results
    
    def get_user(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        return self.users.get(user_id)


class SecureMessagingSystem:
    """Secure messaging system."""
    
    def __init__(self):
        self.direct_messages = {}  # (user1_id, user2_id) -> [messages]
        self.group_messages = {}  # group_id -> [messages]
    
    def send(self, message: Message) -> str:
        """Send direct message."""
        conversation_key = tuple(sorted([message.sender_id, message.recipient_id]))
        
        if conversation_key not in self.direct_messages:
            self.direct_messages[conversation_key] = []
        
        self.direct_messages[conversation_key].append(message)
        
        return message.message_id
    
    def send_to_group(self, message: Message) -> str:
        """Send message to group."""
        if message.group_id not in self.group_messages:
            self.group_messages[message.group_id] = []
        
        self.group_messages[message.group_id].append(message)
        
        return message.message_id
    
    def get_direct_conversation(self, user_id: str, other_user_id: str, limit: int) -> List[Dict[str, Any]]:
        """Get direct conversation history."""
        conversation_key = tuple(sorted([user_id, other_user_id]))
        
        messages = self.direct_messages.get(conversation_key, [])
        
        # Return most recent messages
        recent_messages = messages[-limit:] if len(messages) > limit else messages
        
        return [
            {
                'message_id': msg.message_id,
                'sender_id': msg.sender_id,
                'content': msg.content,
                'created_at': msg.created_at.isoformat(),
                'read_at': msg.read_at.isoformat() if msg.read_at else None,
                'priority': msg.priority.value
            }
            for msg in recent_messages
        ]
    
    def get_group_conversation(self, group_id: str, limit: int) -> List[Dict[str, Any]]:
        """Get group conversation history."""
        messages = self.group_messages.get(group_id, [])
        
        recent_messages = messages[-limit:] if len(messages) > limit else messages
        
        return [
            {
                'message_id': msg.message_id,
                'sender_id': msg.sender_id,
                'content': msg.content,
                'created_at': msg.created_at.isoformat(),
                'read_at': msg.read_at.isoformat() if msg.read_at else None,
                'priority': msg.priority.value
            }
            for msg in recent_messages
        ]


class GroupChatSystem:
    """Group chat management system."""
    
    def __init__(self):
        self.groups = {}  # group_id -> GroupChat
    
    def create(self, group: GroupChat) -> str:
        """Create group chat."""
        self.groups[group.group_id] = group
        return group.group_id
    
    def get_group(self, group_id: str) -> Optional[GroupChat]:
        """Get group by ID."""
        return self.groups.get(group_id)
    
    def add_participant(self, group_id: str, user_id: str) -> bool:
        """Add participant to group."""
        group = self.groups.get(group_id)
        if group and user_id not in group.participants:
            group.participants.append(user_id)
            return True
        return False
    
    def remove_participant(self, group_id: str, user_id: str) -> bool:
        """Remove participant from group."""
        group = self.groups.get(group_id)
        if group and user_id in group.participants:
            group.participants.remove(user_id)
            return True
        return False


class BroadcastSystem:
    """Broadcast messaging system."""
    
    def send(self, message: Message, targets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Send broadcast message to targets."""
        results = []
        
        for target in targets:
            # In production, would actually send to each target
            results.append({
                'target_id': target['user_id'],
                'status': 'sent',
                'message_id': message.message_id
            })
        
        return results


class SecureFileSharing:
    """Secure file sharing system."""
    
    def __init__(self):
        self.files = {}  # file_id -> file metadata
    
    def share(self, sender_id: str, recipient_id: Optional[str], group_id: Optional[str],
              file_data: Dict[str, Any], access_level: str) -> str:
        """Share file securely."""
        file_id = str(uuid.uuid4())
        
        file_metadata = {
            'file_id': file_id,
            'sender_id': sender_id,
            'recipient_id': recipient_id,
            'group_id': group_id,
            'file_name': file_data.get('name', ''),
            'file_size': file_data.get('size', 0),
            'file_type': file_data.get('type', ''),
            'access_level': access_level,
            'shared_at': datetime.utcnow().isoformat(),
            'download_count': 0
        }
        
        self.files[file_id] = file_metadata
        
        return file_id
    
    def get_file(self, file_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get file metadata if user has access."""
        file_metadata = self.files.get(file_id)
        
        if not file_metadata:
            return None
        
        # Check access permissions
        if file_metadata['access_level'] == 'public':
            return file_metadata
        elif file_metadata['access_level'] == 'restricted':
            if user_id == file_metadata['sender_id'] or user_id == file_metadata['recipient_id']:
                return file_metadata
        elif file_metadata['access_level'] == 'confidential':
            if user_id == file_metadata['sender_id']:
                return file_metadata
        
        return None


class IdentityVerificationSystem:
    """Identity verification system."""
    
    def verify(self, user_data: Dict[str, Any], method: str) -> Dict[str, Any]:
        """Verify user using specified method."""
        verification_methods = {
            'email_verification': self._verify_email,
            'phone_verification': self._verify_phone,
            'professional_verification': self._verify_professional,
            'company_verification': self._verify_company
        }
        
        verifier = verification_methods.get(method)
        if verifier:
            return verifier(user_data)
        else:
            return {'verified': False, 'error': 'Unknown verification method'}
    
    def _verify_email(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Verify email address."""
        email = user_data.get('email')
        if not email:
            return {'verified': False, 'error': 'No email provided'}
        
        # Basic email format validation
        import re
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(pattern, email):
            return {'verified': False, 'error': 'Invalid email format'}
        
        # In production, would send verification email
        return {'verified': True, 'method': 'email_verification'}
    
    def _verify_phone(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Verify phone number."""
        phone = user_data.get('phone')
        if not phone:
            return {'verified': False, 'error': 'No phone provided'}
        
        # Basic phone format validation
        import re
        cleaned = re.sub(r'[^0-9]', '', phone)
        if len(cleaned) < 10 or len(cleaned) > 15:
            return {'verified': False, 'error': 'Invalid phone format'}
        
        # In production, would send SMS verification
        return {'verified': True, 'method': 'phone_verification'}
    
    def _verify_professional(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Verify professional credentials."""
        # Check for professional indicators
        has_company = bool(user_data.get('company'))
        has_role = bool(user_data.get('role'))
        has_linkedin = bool(user_data.get('linkedin'))
        
        if has_company and has_role:
            return {'verified': True, 'method': 'professional_verification'}
        else:
            return {'verified': False, 'error': 'Insufficient professional information'}
    
    def _verify_company(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Verify company affiliation."""
        company = user_data.get('company')
        if not company:
            return {'verified': False, 'error': 'No company provided'}
        
        # In production, would verify company existence
        return {'verified': True, 'method': 'company_verification'}


class NotificationSystem:
    """Notification system for users."""
    
    def __init__(self):
        self.user_notifications = {}  # user_id -> [notifications]
    
    def notify_user(self, user_id: str, notification: Dict[str, Any]):
        """Send notification to user."""
        if user_id not in self.user_notifications:
            self.user_notifications[user_id] = []
        
        notification['notification_id'] = str(uuid.uuid4())
        notification['created_at'] = datetime.utcnow().isoformat()
        notification['read'] = False
        
        self.user_notifications[user_id].append(notification)
    
    def get_notifications(self, user_id: str, unread_only: bool = True) -> List[Dict[str, Any]]:
        """Get user notifications."""
        notifications = self.user_notifications.get(user_id, [])
        
        if unread_only:
            notifications = [n for n in notifications if not n['read']]
        
        return notifications
    
    def mark_as_read(self, user_id: str, notification_id: str):
        """Mark notification as read."""
        if user_id in self.user_notifications:
            for notification in self.user_notifications[user_id]:
                if notification['notification_id'] == notification_id:
                    notification['read'] = True
                    break
