# AI Controller Agent Guidelines

## Agent Behavior
- The AI Controller manages system-level AI operations including voice control, device management, and automation workflows
- Agents should follow the principle of least privilege when accessing system resources
- All agent actions must be logged for audit and debugging purposes

## Communication Protocols
- Agents communicate via JSON-RPC over local sockets
- Message format: `{"id": <number>, "method": "<action>", "params": {...}}`
- Responses must include status codes and optional data payloads

## Error Handling
- Agents must implement exponential backoff for failed operations
- Critical failures should trigger system alerts via the notification subsystem
- All exceptions must be caught and logged with full stack traces

## Security Requirements
- Agents must validate all input parameters before processing
- No agent may execute arbitrary code without explicit user approval
- File system access must be restricted to designated directories only

## Performance Guidelines
- Agents should respond to health checks within 100ms
- Memory usage must be monitored and kept under 50MB per agent instance
- CPU utilization should not exceed 25% sustained for background agents

## Development Standards
- All new agents must include unit tests covering 80%+ of functionality
- Code must follow PEP 8 style guidelines with flake8 validation
- Documentation strings are required for all public methods and classes
