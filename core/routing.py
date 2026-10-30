"""Route registry that lets department modules declare routes without the app.

A department module cannot import ``app`` without creating a cycle back through
the bootstrap, so decorators here collect ``(rule, endpoint, view, options)``
instead. ``Router.register`` later hands them to ``app.add_url_rule``, and the
endpoint is the view function's name, exactly as ``@app.route`` derives it.
"""


class Router:
    def __init__(self):
        self._rules = []

    def route(self, rule, **options):
        def decorator(view):
            self._rules.append((rule, view.__name__, view, dict(options)))
            return view
        return decorator

    def get(self, rule, **options):
        options.setdefault("methods", ["GET"])
        return self.route(rule, **options)

    def post(self, rule, **options):
        options.setdefault("methods", ["POST"])
        return self.route(rule, **options)

    def register(self, app):
        for rule, endpoint, view, options in self._rules:
            app.add_url_rule(rule, endpoint=endpoint, view_func=view, **options)


router = Router()
route = router.route
get = router.get
post = router.post
