# Sub-Agent Voice Personas

These JSON files configure voice-driven sub-agent conversations in `subagent_voice.py`. Each persona is a separate sellable script in the voice market.

## Run a persona

```bash
python3 "$HOME/ai-controller/scripts/subagent_voice.py" --persona companion
```

## Persona structure

```json
{
  "name": "Chatty",
  "label": "Chatty — Listener",
  "description": "A calm voice companion...",
  "locked": false,
  "price": "",
  "voice": "joe",
  "model": "qwen2.5-coder:fast",
  "greeting": "Hey, I'm Chatty...",
  "system_prompt": "You are Chatty, a warm voice companion..."
}
```

## Free vs premium

- Free personas: set `"locked": false` and include in the base archive.
- Premium personas: set `"locked": true` and sell as voice-market add-ons.

## Important disclaimer

Personas like Chatty are **conversation companions**, not therapists, doctors, or licensed professionals. Always include a disclaimer in the system prompt and marketing copy.

## Adding new personas

1. Create `personas/<your-persona>.json`.
2. Include a clear system prompt and optional greeting.
3. Set `locked` and `price` if selling it.
4. Test with `python3 subagent_voice.py --persona <your-persona>`.
