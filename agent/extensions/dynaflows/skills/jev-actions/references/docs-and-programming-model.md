# Docs and programming model

## Objective

Apply the System One programming model and read the current TypeSafe docs before designing questions
or writing code.

## Guidance

- Treat Jev, TypeSafe's flagship System One model, as programmable common sense.
  It reads natural language and returns typed answers and probabilities, not generated text or
  explanations.
- Let code own the workflow, and ask Jev only where ordinary code needs semantic understanding.
- Treat the live docs as the source of truth for concepts, prompting guidance, API contracts, SDK
  usage, models, limits and worked examples.
  Read them as part of the task.
- Start at the [documentation index](https://docs.typesafe.ai/llms.txt), and make targeted reads
  rather than loading the whole site.
- Fetch Markdown by appending `.md` to a page path,
  for example
  [how to build with TypeSafe](https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md).
  Convert extensionless page links from the index to `.md`, and resolve relative links against
  `https://docs.typesafe.ai`.
- Before writing an integration, read the current API or chosen SDK page and the question guidance
  relevant to the design.
- For a new workflow, also read the closest cookbook; it often shows a better decomposition than a
  generic classifier.
- If the index is unavailable, use the pages below or the site's navigation.
  If Markdown fetching fails, fetch the normal page.
- If live access is unavailable, use local docs or the installed SDK's types, state that limitation,
  and do not invent version-dependent details.
- Start from these pages for each kind of work, then follow the relevant details:
  - Programming model: [System One](https://docs.typesafe.ai/concepts/system-one.md),
    [building guide](https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md).
  - What to build: [use-case map](https://docs.typesafe.ai/concepts/use-case-map.md), then relevant
    cookbooks from the index.
  - Inputs and questions: [state](https://docs.typesafe.ai/concepts/state.md),
    [primitives](https://docs.typesafe.ai/primitives.md),
    then the chosen primitive's page.
  - Uncertainty: [confidence](https://docs.typesafe.ai/confidence.md),
    [confidence routing](https://docs.typesafe.ai/patterns/confidence-routing.md).
  - Model weak spots: [models](https://docs.typesafe.ai/models.md), then the jaggedness page for the
    pinned version,
    such as [Jev 1.13](https://docs.typesafe.ai/model-jaggedness/jev-1.13.md).
  - Coding agents: [Jev with coding agents](https://docs.typesafe.ai/introduction/coding-agents.md).
  - API code: [HTTP API](https://docs.typesafe.ai/api.md),
    [Python SDK](https://docs.typesafe.ai/sdk/python.md),
    or [JavaScript SDK](https://docs.typesafe.ai/sdk/javascript.md).
  - Older integrations: [migration guide](https://docs.typesafe.ai/migrating-to-v1.md) and the
    installed SDK's current reference.
