Resolve GitHub review and re-review recipients for a PR, returning a configured override or discovering appropriate reviewers from repository and PR evidence. Returns recipients without requesting reviews.

Prefer appropriate existing requests and previous human reviewers for re-review. Do not select someone solely from dismissed reviews without another supported reason. Do not assume the authenticated actor is the PR author or exclude them on that basis.

When PR history does not establish suitable recipients, inspect changed paths and repository ownership rules at the target revision. If ownership does not resolve the selection, assess relevant recent maintainers from path-specific history or merged PR reviews, with a bounded targeted search. Avoid unrelated popular contributors and the entire contributor history.
