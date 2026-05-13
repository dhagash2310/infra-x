---
name: Bug report
about: Report something that doesn't work the way it should
labels: bug
---

**What happened**
A clear description of the bug.

**What you expected**
What you thought should have happened.

**Reproduction**
Exact command(s) you ran:

```bash
infra-x generate -b ... -p "..." -o ./out
```

**Output**
The full output, including any error message. Use a code block.

```
(paste here)
```

**Does it reproduce with `--no-llm`?**
- [ ] Yes, the bug shows up even in deterministic mode
- [ ] No, only with the LLM in the loop
- [ ] Haven't tested

**Environment**
- infra-x version: (run `infra-x version`)
- Python version: (run `python --version`)
- Terraform version: (run `terraform version`)
- LLM provider: ollama / anthropic / openai / n/a
- OS: macOS / Linux / Windows

**Anything else**
Stack traces, screenshots, related issues, etc.
