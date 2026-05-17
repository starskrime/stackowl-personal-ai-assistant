/**
 * Contract for channel-specific command registration.
 * Telegram implements this; future Slack/Discord/WhatsApp adapters
 * implement their own version. GatewayCore can call register(REGISTRY)
 * on all adapters uniformly.
 */
export interface ChannelCommandRouter {
  /** Wire all registry commands onto the channel's command system. */
  register(bot: unknown): void;
  /** Sync the channel's command menu with the current registry. Optional. */
  updateMenu?(bot: unknown): Promise<void>;
}
