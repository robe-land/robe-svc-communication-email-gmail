# Robe Service: Communication Email Gmail

This independently deployable service receives email retrieval requests and
publishes normalized email retrieval events. Its first implementation
increment uses synthetic Gmail-shaped fixtures and the local Pub/Sub
emulator; it does not access Gmail or require OAuth.

> Maintained by [Robe Land](https://github.com/robe-land).
>
> Original work is licensed under [LICENSE](LICENSE). Third-party
> materials retain their applicable licenses and copyright notices; see
> the notices accompanying those materials and the repository's
> third-party attribution index, where applicable.

## Development

Create a Python virtual environment and install the test extra:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
```

Run local verification with `./scripts/verify`. The local synthetic
Pub/Sub integration scenario and its required Platform emulator setup are
documented alongside the service composition.
