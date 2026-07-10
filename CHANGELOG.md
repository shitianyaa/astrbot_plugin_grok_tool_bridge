# Changelog

## v0.2.1

- Simplified `/grok生图` to text-to-image only and removed the unavailable
  image-to-image, mask, and edit-model paths.
- Parse only an optional leading image count; ratio-like text now remains in
  the prompt and no `size` or `aspect_ratio` field is forced.
- Reject attached images with a clear unsupported message and remove the
  unused Pillow dependency.

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
