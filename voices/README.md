# AI Controller Voice Packs

Voice packs are self-contained Piper TTS models that drop into this folder and appear automatically in the on-screen keyboard's **VOICE PROFILES** shelf.

## Pack structure

```
voices/
└── joe/                          # voice ID (lowercase, no spaces)
    ├── config.json               # metadata shown in the UI
    ├── en_US-joe-medium.onnx     # Piper model
    └── en_US-joe-medium.onnx.json # Piper model config
```

## config.json

```json
{
  "name": "Joe",
  "label": "Joe",
  "engine": "piper",
  "model": "en_US-joe-medium.onnx",
  "description": "Male local neural voice via Piper",
  "locked": false,
  "price": ""
}
```

| Field | Purpose |
|-------|---------|
| `name` | Display name in menus and announcements. |
| `label` | Text shown on the keyboard button. |
| `engine` | Must be `piper` for now. |
| `model` | Filename of the `.onnx` model inside the pack. |
| `description` | Tooltip / announcement text. |
| `locked` | `true` for premium packs sold separately. |
| `price` | Optional price string, e.g. `"$5"`. |

## Free vs. premium

- Free packs: set `"locked": false` and include the `.onnx` model.
- Premium packs: set `"locked": true`. The button appears grayed out with a lock icon until the buyer drops the model files in after purchase.

## Unlocking a premium pack

After purchase, drop the model files into `voices/<pack>/` and run:

```bash
python3 "$HOME/ai-controller/scripts/voice_toggle.py" --unlock <pack>
```

The keyboard shelf refreshes automatically.

## Where to get Piper voices

Piper voices are MIT-licensed and can be used commercially. Download pre-trained voices from the [Piper voices release page](https://github.com/rhasspy/piper/releases) or train your own with a Piper-compatible dataset.

## Gumroad-ready workflow

1. Train or source a Piper voice you have rights to.
2. Create a `config.json` with `"locked": true` and a price.
3. Zip the pack folder (without the `.onnx` model for the "locked" listing, or with it for the paid download).
4. Sell the zip on Gumroad; buyers unzip it into `$HOME/ai-controller/voices/` and run the unlock command.
