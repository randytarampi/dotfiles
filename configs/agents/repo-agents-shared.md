## Repository Guidance

These policies apply to work in every repository.

### Verification

- Run the repository's canonical verification command before claiming success.
- If verification fails, fix it before reporting the work as complete.

### Commits and pushes

- Keep one concern per commit.
- Use Conventional Commits (`type(scope): description`).
- Never push unless the user explicitly authorizes it.

### Writing and ambiguity

- Use Canadian English in prose and Canadian Press style for formal artifacts.
- Ask before implementing when a flag or name has ambiguous semantics; do not guess when the cost of being wrong is high.

### Delegation and planning

- For unknown scope, delegate bounded discovery first; read expected edit targets directly.
- When changing AI tooling, assess every configured tool up front and enumerate the full tool fleet.
- Keep repository-specific facts and implementation details in the repository's own guidance and documentation.

### Artifacts

- Probe binary artifacts with appropriate local tools before concluding they are unreadable; never ask the user to resend an unreadable artifact.
