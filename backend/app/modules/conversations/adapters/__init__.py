from app.modules.conversations.adapters.base import ChannelAdapter
from app.modules.conversations.adapters.email import EmailAdapter
from app.modules.conversations.models import Channel

# Every adapter, by channel. The mode test walks this, so a new adapter is covered once it is here.
ADAPTERS: dict[Channel, ChannelAdapter] = {Channel.EMAIL: EmailAdapter()}
