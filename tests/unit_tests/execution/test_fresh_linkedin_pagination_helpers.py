"""Pagination helper unit tests for ``fresh_linkedin`` (PG-D2 / LLD §4.1).

Two module-private helpers back the per-op pagination validation introduced
in the Phase 2.5 pagination-completeness work:

- ``_require_paired(a, b, names)`` — enforces the "both or neither" rule for
  ``start``+``pagination_token`` (profile/company posts) and ``page``
  +``pagination_token`` (comments).
- ``_coerce_numeric_string(value, field_name)`` — normalises ``start`` / ``page``
  to a non-negative integer string (vendor-wire shape), rejecting everything
  the vendor would reject a few hundred ms later.

Pure functions. Zero mocks. Tested directly per the unit-testing blueprint
§5.4 escape hatch: these carry meaningful branching logic, are colocated with
the ops that use them, and extracting further would be disproportionate churn
(they'd just move to another file with the same signatures).
"""

from __future__ import annotations

import pytest

from server.core.exceptions import ProviderError


# The helpers live in the provider module. Import lazily so the whole file
# doesn't error-out before the implementation lands (TDD red phase).
def _helpers():
    from server.execution.providers import fresh_linkedin as mod
    return mod._require_paired, mod._coerce_numeric_string


# ---------------------------------------------------------------------------
# _require_paired — both-or-neither for (offset, token) pairs
# ---------------------------------------------------------------------------


class TestRequirePairedAcceptsBothOrNeither:
    def test_both_none_is_ok(self) -> None:
        require_paired, _ = _helpers()
        # Must not raise.
        require_paired(None, None, ("start", "pagination_token"))

    def test_both_provided_is_ok(self) -> None:
        require_paired, _ = _helpers()
        require_paired("10", "tok", ("start", "pagination_token"))

    def test_both_empty_string_treated_as_none(self) -> None:
        """Empty strings are falsy; the rule cares about truthiness, not
        explicit ``None``. This keeps callers free to pass ``""`` to mean
        'unset' without tripping the validator."""
        require_paired, _ = _helpers()
        require_paired("", "", ("start", "pagination_token"))


class TestRequirePairedRejectsLonelyValue:
    def test_first_only_raises_400(self) -> None:
        require_paired, _ = _helpers()
        with pytest.raises(ProviderError) as exc_info:
            require_paired("10", None, ("start", "pagination_token"))
        assert exc_info.value.status_code == 400

    def test_second_only_raises_400(self) -> None:
        require_paired, _ = _helpers()
        with pytest.raises(ProviderError) as exc_info:
            require_paired(None, "tok", ("start", "pagination_token"))
        assert exc_info.value.status_code == 400

    def test_error_message_names_both_fields(self) -> None:
        """Callers need to see which pair mis-tripped (``start`` +
        ``pagination_token`` vs ``page`` + ``pagination_token``)."""
        require_paired, _ = _helpers()
        with pytest.raises(ProviderError) as exc_info:
            require_paired("10", None, ("start", "pagination_token"))
        msg = str(exc_info.value)
        assert "start" in msg
        assert "pagination_token" in msg

    def test_error_message_uses_caller_supplied_names(self) -> None:
        """The helper is generic across ``(start, token)`` and ``(page, token)``;
        the error must reflect whichever pair the caller passed in."""
        require_paired, _ = _helpers()
        with pytest.raises(ProviderError) as exc_info:
            require_paired(None, "tok", ("page", "pagination_token"))
        assert "page" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _coerce_numeric_string — vendor-wire normalisation + fail-fast validation
# ---------------------------------------------------------------------------


class TestCoerceNumericStringHappyPath:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (10, "10"),
            ("10", "10"),
            (0, "0"),       # LLD decision 13.1: 0 permitted; vendor decides
            ("0", "0"),
            (999999, "999999"),
        ],
    )
    def test_valid_values_coerced_to_str(self, value, expected: str) -> None:
        _, coerce = _helpers()
        assert coerce(value, "start") == expected


class TestCoerceNumericStringRejectsInvalid:
    @pytest.mark.parametrize(
        "bad",
        [
            -1,      # negative int
            "abc",   # non-numeric string
            "",      # empty string
            "1.5",   # float-shaped string
            "10a",   # mixed
            "-10",   # signed string
            " 10",   # leading whitespace
            1.5,     # float
            None,    # None is not a numeric type
            [],      # list
            {},      # dict
        ],
    )
    def test_invalid_value_raises_400(self, bad) -> None:
        _, coerce = _helpers()
        with pytest.raises(ProviderError) as exc_info:
            coerce(bad, "start")
        assert exc_info.value.status_code == 400

    def test_bool_true_rejected_despite_being_int_subclass(self) -> None:
        """``isinstance(True, int)`` is True in Python — the helper must
        explicitly filter bools before the int branch or a caller typing
        ``page=True`` would silently coerce to ``"1"`` (LLD decision 13.2)."""
        _, coerce = _helpers()
        with pytest.raises(ProviderError) as exc_info:
            coerce(True, "page")
        assert exc_info.value.status_code == 400

    def test_bool_false_rejected(self) -> None:
        _, coerce = _helpers()
        with pytest.raises(ProviderError):
            coerce(False, "page")


class TestCoerceNumericStringErrorMessage:
    def test_message_names_the_field(self) -> None:
        _, coerce = _helpers()
        with pytest.raises(ProviderError) as exc_info:
            coerce("abc", "start")
        assert "start" in str(exc_info.value)

    def test_message_includes_offending_input(self) -> None:
        """Callers debugging a 400 need to see what they sent."""
        _, coerce = _helpers()
        with pytest.raises(ProviderError) as exc_info:
            coerce("abc", "page")
        assert "abc" in str(exc_info.value)
