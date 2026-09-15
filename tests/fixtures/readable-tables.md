# Readable comparison tables

This fixture contains prose, not only short status values.

| Host | Good use | Main risk | Recommendation |
| --- | --- | --- | --- |
| Laptop | Browser or IDE access to development environments and an existing working directory. | Direct access can affect desktop settings and user files. A laptop is not an always-on control plane. | Use it as a client first. Consider a restricted external workspace later. |
| Agent host | Development work near current repositories and tools. | Broad home-directory access exposes credentials and creates self-modification risk. | Pilot in a dedicated workspace. Do not attach the whole home or service controls. |
| Media host | Inspect configuration, prepare changes, and review logs and service status. | Media mounts and live service restarts make mistakes costly. | Add last, read-only first. Keep production separate from disposable compute. |

## Links, code, and empty cells

| [Documentation](https://example.com/docs) | Command | Empty | Long token |
| --- | :---: | ---: | --- |
| [Read the guide](https://example.com/guide) | `check --read-only` | | abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz |

## Compact data

| Item | Count |
| --- | ---: |
| First | 12 |
| Second | 34 |
