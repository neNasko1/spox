import typing
from dataclasses import dataclass
from typing import ClassVar, Set, TypeVar

import numpy as np
import numpy.typing as npt
import onnx

from ._shape import Shape, SimpleShape
from ._utils import _DTYPE_TO_TENSOR_TYPE, dtype_to_tensor_type, tensor_type_to_dtype

T = TypeVar("T")
S = TypeVar("S")


@dataclass(frozen=True)
class Type:
    """
    Base class for representing Steelix Types, which are based on ONNX types.

    The key methods include ``from_onnx`` and ``to_onnx`` to facilitate conversion between formats.

    Additionally, Types support membership testing (with <= and >=) and least-matching unions (with |).
    In essence, membership testing tests for subset types, and unions are the least general superset of both types.

    >>> Tensor(numpy.int64, (1, 2, 3)) <= Tensor(numpy.int64)
    True
    >>> Tensor(numpy.int64, (1, 2, 3)) <= Tensor(numpy.int32)
    False
    >>> Tensor(numpy.int64, (3, 5)) | Tensor(numpy.int64, ())
    Tensor(elem_type=int64, shape=None)
    >>> Tensor(numpy.int64, ('M', 3)) | Tensor(numpy.int64, ('M', 'N'))
    Tensor(elem_type=int64, shape=('M', None))
    """

    @classmethod
    def _from_onnx(cls, proto: onnx.TypeProto) -> "Type":
        """
        Parameters
        ----------
        proto
            Protobuf object to translate from.
        Returns
        -------
        Type
            Respective subtype of Type representing the ONNX type in the protobuf object.
        Raises
        ------
        ValueError
            If the passed protobuf does not contain any of the expected fields (tensor, sequence, optional).
        """
        if proto.HasField("tensor_type"):
            return Tensor(
                tensor_type_to_dtype(proto.tensor_type.elem_type),
                Shape.from_onnx(proto.tensor_type.shape).to_simple()
                if proto.tensor_type.HasField("shape")
                else None,
            )
        elif proto.HasField("sequence_type"):
            return Sequence(Type._from_onnx(proto.sequence_type.elem_type))
        elif proto.HasField("optional_type"):
            return Optional(Type._from_onnx(proto.optional_type.elem_type))
        raise ValueError(
            f"Cannot get Type from invalid protobuf (not tensor, sequence or optional): {proto}"
        )

    def _assert_concrete(self, *, _traceback_name: str = "?"):
        """
        Function used by the build process to check if a type is well-specified (e.g. Tensor shape is defined).
        Inheritors of Type should throw if they do not specify enough information to be accepted as Model input/outputs.
        """
        return self

    @property
    def _is_concrete(self) -> bool:
        try:
            self._assert_concrete()
        except Exception:
            return False
        else:
            return True

    def unwrap_tensor(self) -> "Tensor":
        """
        Returns
        -------
        Tensor
            If this Type is a Tensor, this function returns `self`.
        Raises
        ------
        TypeError
            If the type isn't a Tensor.
        """
        if not isinstance(self, Tensor):
            raise TypeError(
                f"Cannot unwrap requested Tensor type, as it is not a Tensor: {self}"
            )
        return self

    def _to_onnx(self) -> onnx.TypeProto:
        """Translate ``self`` into an ONNX TypeProto."""
        raise TypeError(
            f"Cannot generate ONNX TypeProto for {self} (not implemented or bad type)."
        )

    def _to_onnx_value_info(
        self,
        name: str,
        doc_string: str = "",
        *,
        concrete: bool = False,
        _traceback_name: str = "?",
    ) -> onnx.ValueInfoProto:
        """Translation of ``self`` into an ONNX ValueInfoProto"""
        if concrete:
            self._assert_concrete(_traceback_name=_traceback_name)
        return onnx.helper.make_value_info(
            name,
            self._to_onnx(),
            doc_string,
        )

    def __le__(self, other: "Type") -> bool:
        """
        Compare Types for membership.
        An Unknown field (like an unspecified Tensor shape) is treated as "any" in this comparison.
        """
        if not isinstance(other, Type):
            return NotImplemented
        return self == Type() or other == Type() or self == other

    def __or__(self, other: "Type") -> "Type":
        """Type set "intersection". Returns a minimally-constrained type matching both parameters."""
        if not isinstance(other, Type):
            return NotImplemented
        return self if self == other else Type()


@dataclass(frozen=True)
class Tensor(Type):
    """
    Represents a ``Tensor`` of given ``elem_type`` and ``shape``.

    Numpy scalar types (``numpy.generic``) are used to store the element types.

    The ``shape`` may be passed in as a simple tuple (``SimpleShape``)
    of integers (constants), strings (parameters) and Nones (unknown values).
    Alternatively, an explicit ``Shape`` object may be constructed, which is used internally.

    If you want to specify that dimensions will be equal, you can use the same parameter strings.
    However, this is not very strictly enforced.
    """

    elem_type: typing.Type[np.generic]
    _shape: Shape

    VALID_TYPES: ClassVar[Set[typing.Type[np.generic]]] = {
        dtype.type for dtype in _DTYPE_TO_TENSOR_TYPE
    }

    def __init__(
        self,
        elem_type: npt.DTypeLike,
        shape: SimpleShape = None,
    ):
        """
        Raises
        ------
        TypeError
            If the passed ``elem_type`` is not a proper element type (not a member of ``Tensor.VALID_TYPES``).
        """
        if elem_type is None or np.dtype(elem_type).type not in self.VALID_TYPES:
            raise TypeError(
                f"'{elem_type}' is not a proper Tensor elem type, the allowed Tensor elem_types are Tensor.VALID_TYPES"
                f" ('numpy.generic' with exceptions, like 'object')."
            )
        rich_shape = Shape.from_simple(shape)
        if not isinstance(rich_shape, Shape):
            raise TypeError(
                "Tensor shape must be of type Shape (or passed in as a simple-representation tuple/None)."
            )
        object.__setattr__(self, "elem_type", np.dtype(elem_type).type)
        object.__setattr__(self, "_shape", rich_shape)

    @property
    def shape(self) -> SimpleShape:
        return self._shape.to_simple()

    def _to_onnx(self) -> onnx.TypeProto:
        return onnx.helper.make_tensor_type_proto(
            dtype_to_tensor_type(self.elem_type), self.shape
        )

    def _assert_concrete(self, *, _traceback_name: str = "?"):
        if self._shape is None:
            raise ValueError(
                f"Tensor {self} does not specify the shape -- in {_traceback_name}."
            )
        return self

    def __repr__(self):
        return f"{type(self).__name__}(elem_type={self.elem_type.__name__}, shape={self.shape})"

    def __str__(self):
        dims = self.shape
        dims_repr = (
            "".join(f"[{dim if dim is not None else '?'}]" for dim in dims)
            if dims is not None
            else "[...]"
        )
        return f"{self.elem_type.__name__.rstrip('_')}" + dims_repr

    def __le__(self, other: Type) -> bool:
        if not isinstance(other, Type):
            return NotImplemented
        if other == Type() or self == other:
            return True
        if not isinstance(other, Tensor):
            return False
        return (
            issubclass(self.elem_type, other.elem_type) and self._shape <= other._shape
        )

    def __or__(self, other):
        if not isinstance(other, Type):
            return NotImplemented
        if not isinstance(other, Tensor) or self.elem_type != other.elem_type:
            return Type()
        return Tensor(self.elem_type, (self._shape | other._shape).to_simple())


@dataclass(frozen=True)
class Sequence(Type):
    elem_type: Type

    def _to_onnx(self) -> onnx.TypeProto:
        return onnx.helper.make_sequence_type_proto(self.elem_type._to_onnx())

    def __repr__(self):
        return f"{type(self).__name__}(elem_type={self.elem_type!r}"

    def __str__(self):
        return f"[{self.elem_type}]"

    def __le__(self, other: Type) -> bool:
        if not isinstance(other, Type):
            return NotImplemented
        if other == Type() or self == other:
            return True
        if not isinstance(other, Sequence):
            return False
        return self.elem_type <= other.elem_type

    def __or__(self, other):
        if not isinstance(other, Type):
            return NotImplemented
        if not isinstance(other, Sequence):
            return Type()
        return Sequence(self.elem_type | other.elem_type)


@dataclass(frozen=True)
class Optional(Type):
    elem_type: Type

    def _to_onnx(self) -> onnx.TypeProto:
        return onnx.helper.make_optional_type_proto(self.elem_type._to_onnx())

    def __repr__(self):
        return f"{type(self).__name__}(elem_type={self.elem_type!r}"

    def __str__(self):
        return f"{self.elem_type}?"

    def __le__(self, other: Type) -> bool:
        if not isinstance(other, Type):
            return NotImplemented
        if other == Type() or self == other:
            return True
        if not isinstance(other, Optional):
            return False
        return self.elem_type <= other.elem_type

    def __or__(self, other):
        if not isinstance(other, Type):
            return NotImplemented
        if not isinstance(other, Optional):
            return Type()
        return Optional(self.elem_type | other.elem_type)


def type_match(first: typing.Optional[Type], second: typing.Optional[Type]) -> bool:
    return first is None or second is None or first <= second or second <= first
