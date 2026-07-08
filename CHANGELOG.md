# Changelog

## v0.2.0

- Added `/grok生图` image generation (text-to-image and image-to-image),
  ported and trimmed from astrbot_plugin_grok_suite (author: 沐沐沐倾).
- New image config keys: grok_api_url, grok_api_key, grok_image_model,
  grok_edit_model, save_media, and user/group white/blacklists.
- Added `@bot`/wake gate (`require_at_or_wake`) for auto bridging.

## v0.1.0

- Initial Grok/xAI tool bridge for AstrBot builtin tools.
- Added automatic, manual, proactive task, recent file, scheduled file, and diagnostic flows.
- Kept automatic mode limited to low-risk tools by default.
