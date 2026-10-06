from textwrap import dedent

from execloc.gold import gold_from_patch, is_test_file

PKG_SRC = dedent(
    """\
    import os

    X = 1


    class Foo:
        attr = 2

        def bar(self):
            a = 1
            b = 2
            return a + b

        @property
        def baz(self):
            return 3


    def top():
        def inner():
            return 4
        return inner()
    """
)
# Line numbers in PKG_SRC:
#  1 import os            9 def bar        14 @property       19 def top
#  3 X = 1               10 a = 1          15 def baz         20 def inner
#  6 class Foo           11 b = 2          16 return 3        21 return 4
#  7 attr = 2            12 return a + b                      22 return inner()

UTIL_SRC = dedent(
    """\
    def helper():
        return 1
    """
)

SOURCES = {"pkg/mod.py": PKG_SRC, "pkg/util.py": UTIL_SRC}


def get_source(path):
    return SOURCES[path]


def gold(patch):
    return gold_from_patch("x__y-1", dedent(patch), get_source)


def test_modify_only_hunk():
    g = gold(
        """\
        diff --git a/pkg/mod.py b/pkg/mod.py
        --- a/pkg/mod.py
        +++ b/pkg/mod.py
        @@ -9,4 +9,4 @@ class Foo:
             def bar(self):
                 a = 1
        -        b = 2
        +        b = 3
                 return a + b
        """
    )
    assert g["files"] == ["pkg/mod.py"]
    assert g["functions"] == [{"file": "pkg/mod.py", "name": "Foo.bar"}]
    assert g["lines"] == [{"file": "pkg/mod.py", "start": 11, "end": 11}]


def test_add_only_hunk_uses_enclosing_function():
    g = gold(
        """\
        diff --git a/pkg/mod.py b/pkg/mod.py
        --- a/pkg/mod.py
        +++ b/pkg/mod.py
        @@ -10,2 +10,3 @@ class Foo:
                 a = 1
        +        a += 1
                 b = 2
        """
    )
    assert g["functions"] == [{"file": "pkg/mod.py", "name": "Foo.bar"}]
    # The two pre-patch lines that bracket the insertion.
    assert g["lines"] == [{"file": "pkg/mod.py", "start": 10, "end": 11}]


def test_add_only_hunk_in_nested_function():
    g = gold(
        """\
        diff --git a/pkg/mod.py b/pkg/mod.py
        --- a/pkg/mod.py
        +++ b/pkg/mod.py
        @@ -20,2 +20,3 @@ def top():
             def inner():
        +        print("hi")
                 return 4
        """
    )
    assert g["functions"] == [{"file": "pkg/mod.py", "name": "top.inner"}]


def test_add_new_method_between_methods_has_no_function():
    # Insertion between two methods is in the class body, not inside a function.
    g = gold(
        """\
        diff --git a/pkg/mod.py b/pkg/mod.py
        --- a/pkg/mod.py
        +++ b/pkg/mod.py
        @@ -12,2 +12,5 @@ class Foo:
                 return a + b

        +    def new(self):
        +        pass
        +
        """
    )
    assert g["functions"] == []
    assert g["lines"] == [{"file": "pkg/mod.py", "start": 13, "end": 14}]


def test_multi_file_patch_excludes_tests():
    g = gold(
        """\
        diff --git a/pkg/mod.py b/pkg/mod.py
        --- a/pkg/mod.py
        +++ b/pkg/mod.py
        @@ -15,2 +15,2 @@ class Foo:
             def baz(self):
        -        return 3
        +        return 30
        diff --git a/pkg/util.py b/pkg/util.py
        --- a/pkg/util.py
        +++ b/pkg/util.py
        @@ -1,2 +1,2 @@
         def helper():
        -    return 1
        +    return 2
        diff --git a/pkg/tests/test_mod.py b/pkg/tests/test_mod.py
        --- a/pkg/tests/test_mod.py
        +++ b/pkg/tests/test_mod.py
        @@ -1,1 +1,1 @@
        -assert True
        +assert 1
        """
    )
    assert g["files"] == ["pkg/mod.py", "pkg/util.py"]
    assert g["functions"] == [
        {"file": "pkg/mod.py", "name": "Foo.baz"},
        {"file": "pkg/util.py", "name": "helper"},
    ]
    assert g["lines"] == [
        {"file": "pkg/mod.py", "start": 16, "end": 16},
        {"file": "pkg/util.py", "start": 2, "end": 2},
    ]


def test_module_level_change_has_no_function():
    g = gold(
        """\
        diff --git a/pkg/mod.py b/pkg/mod.py
        --- a/pkg/mod.py
        +++ b/pkg/mod.py
        @@ -1,3 +1,3 @@
         import os

        -X = 1
        +X = 2
        """
    )
    assert g["files"] == ["pkg/mod.py"]
    assert g["functions"] == []
    assert g["lines"] == [{"file": "pkg/mod.py", "start": 3, "end": 3}]


def test_decorator_line_maps_to_function_and_ranges_merge():
    g = gold(
        """\
        diff --git a/pkg/mod.py b/pkg/mod.py
        --- a/pkg/mod.py
        +++ b/pkg/mod.py
        @@ -14,3 +14,3 @@
        -    @property
        -    def baz(self):
        -        return 3
        +    @staticmethod
        +    def baz():
        +        return 3
        """
    )
    assert g["functions"] == [{"file": "pkg/mod.py", "name": "Foo.baz"}]
    assert g["lines"] == [{"file": "pkg/mod.py", "start": 14, "end": 16}]


def test_new_file_contributes_only_to_files():
    g = gold(
        """\
        diff --git a/pkg/new.py b/pkg/new.py
        new file mode 100644
        --- /dev/null
        +++ b/pkg/new.py
        @@ -0,0 +1,1 @@
        +Y = 1
        """
    )
    assert g == {"instance_id": "x__y-1", "files": ["pkg/new.py"], "functions": [], "lines": []}


def test_is_test_file():
    assert is_test_file("django/tests/foo.py")
    assert is_test_file("pkg/test_x.py")
    assert is_test_file("conftest.py")
    assert not is_test_file("django/test/utils.py")
    assert not is_test_file("sympy/testing/pytest.py")
    assert not is_test_file("sympy/core/basic.py")
    assert not is_test_file("pytest/src/_pytest/python.py")
