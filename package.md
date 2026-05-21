You are working in my Rocket.Chat mock project.

Goal:
Refactor the Rocket.Chat integration so it becomes portable/movable and can be migrated cleanly into another project, specifically the NexBI/Open WebUI deployment project.

Do not migrate it yet. First make this implementation self-contained, documented, and easy to transplant.

Target future project:
E:\Jobbing\rorck\nexbi\deployment\nex-bi-ovh-deployment

Future integration style:
The target project uses isolated integrations like its JupyterLab setup:
- dedicated backend route namespace
- env-driven configuration
- frontend iframe/entry component
- Docker/runtime config separated from app logic
- no hardcoded local URLs
- no hidden dependencies on mock-only files

Tasks:
1. Audit the current Rocket.Chat mock implementation.
2. Identify all files, routes, components, services, env vars, Docker config, and assumptions it depends on.
3. Refactor it into a portable module layout:
   - backend integration code grouped clearly
   - frontend components grouped clearly
   - shared types/constants grouped clearly
   - config/env access centralized
   - no hardcoded hostnames, ports, credentials, or tokens
4. Add or update documentation:
   - required env vars
   - backend routes
   - frontend entry points
   - Docker/service requirements
   - migration checklist for moving into NexBI/Open WebUI
5. Add safe defaults and feature flags:
   - `ROCKETCHAT_ENABLED`
   - `ROCKETCHAT_BASE_URL`
   - `ROCKETCHAT_INTERNAL_URL`
   - auth/token variables as needed
6. Make the integration relocatable:
   - avoid absolute imports tied to mock app internals
   - isolate framework-specific glue
   - make API paths configurable
   - keep business logic separate from UI rendering
7. Preserve current mock functionality.
8. Add a `MIGRATION_NOTES.md` or equivalent that explains exactly how to move this integration into the NexBI project.

Important constraints:
- Do not break the existing mock project.
- Do not hardcode NexBI paths inside runtime code.
- Do not mix Rocket.Chat code into unrelated mock app files unless it is only a small registration hook.
- Keep changes small and reviewable.
- Avoid broad rewrites unless required for portability.

Expected final output:
- Summary of what was made portable
- Files changed
- Env vars required
- How to run the mock after refactor
- Exact migration checklist for copying into NexBI/Open WebUI
