# Contributing to memory-hook

Thank you for your interest in contributing to `memory-hook`! We welcome contributions to help improve cross-provider AI agent lifecycle coordination.

## Development Setup

1. **Fork and Clone:**
   ```bash
   git clone https://github.com/memory-hook/memory-hook.git
   cd memory-hook
   ```

2. **Set Up Local Python Environment:**
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate

   # Install package in editable development mode
   pip install -e .
   ```

3. **Code Style & Formatting:**
   - Adhere to `.editorconfig` rules (UTF-8, 4 spaces for Python, 2 spaces for JSON/YAML/Markdown, LF line endings).
   - Keep code clean, typed, and well-documented.

## Testing Guidelines

Before opening a pull request, verify that all unit tests pass:

```bash
python -m unittest discover tests
```

To add new tests, place them under `tests/test_<feature>.py` using standard `unittest.TestCase`.

## Submitting Pull Requests

1. Create a feature branch:
   ```bash
   git checkout -b feat/my-new-feature
   ```
2. Commit your changes with clear, descriptive commit messages.
3. Ensure all unit tests pass with zero failures.
4. Push your branch to GitHub and submit a Pull Request targeting `main`.

## Reporting Issues

- **Bugs:** Open a GitHub issue using the Bug Report template, detailing your OS, Python version, agent provider, and reproduction steps.
- **Features:** Open a GitHub issue using the Feature Request template, explaining the motivation, use case, and proposed provider integration.
