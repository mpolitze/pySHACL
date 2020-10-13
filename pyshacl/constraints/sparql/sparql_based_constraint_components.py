# -*- coding: utf-8 -*-
"""
https://www.w3.org/TR/shacl/#sparql-constraint-components
"""
import typing

from typing import Any, Dict, List, Set, Tuple, Type, Union

import rdflib

from pyshacl.constraints.constraint_component import ConstraintComponent, CustomConstraintComponent
from pyshacl.consts import SH, RDF_type, SH_ask, SH_message, SH_select, SH_ConstraintComponent
from pyshacl.errors import ConstraintLoadError, ValidationFailure
from pyshacl.helper import get_query_helper_cls
from pyshacl.pytypes import GraphLike


if typing.TYPE_CHECKING:
    from pyshacl.shape import Shape
    from pyshacl.shapes_graph import ShapesGraph


SH_SPARQLSelectValidator = SH.term('SPARQLSelectValidator')
SH_SPARQLAskValidator = SH.term('SPARQLAskValidator')


class BoundShapeValidatorComponent(ConstraintComponent):
    def __init__(self, constraint, shape: 'Shape', validator):
        """
        Create a new custom constraint, by applying a ConstraintComponent and a Validator to a Shape
        :param constraint: The source ConstraintComponent, this is needed to bind the parameters in the query_helper
        :type constraint: SPARQLConstraintComponent
        :param shape:
        :type shape: Shape
        :param validator:
        :type validator: AskConstraintValidator | SelectConstraintValidator
        """
        super(BoundShapeValidatorComponent, self).__init__(shape)
        self.constraint = constraint
        self.validator = validator
        params = constraint.parameters
        SPARQLQueryHelper = get_query_helper_cls()
        self.query_helper = SPARQLQueryHelper(
            self.shape, validator.node, validator.query_text, params, messages=validator.messages
        )
        # Setting self.shape into QueryHelper automatically applies query_helper.bind_params and bind_messages
        self.query_helper.collect_prefixes()

    @classmethod
    def constraint_parameters(cls):
        # TODO:coverage: this is never used for this constraint?
        return []

    @classmethod
    def constraint_name(cls):
        return "ConstraintComponent"

    @classmethod
    def shacl_constraint_class(cls):
        # TODO:coverage: this is never used for this constraint?
        return SH_ConstraintComponent

    def make_generic_messages(self, datagraph: GraphLike, focus_node, value_node) -> List[rdflib.Literal]:
        return [rdflib.Literal("Parameterised SHACL Query generated constraint validation reports.")]

    def evaluate(self, target_graph: GraphLike, focus_value_nodes: Dict, _evaluation_path: List):
        """
        :type focus_value_nodes: dict
        :type target_graph: rdflib.Graph
        """
        reports = []
        non_conformant = False
        extra_messages = self.constraint.messages or []
        rept_kwargs = {
            # TODO, determine if we need sourceConstraint here
            #  'source_constraint': self.validator.node,
            'constraint_component': self.constraint.node,
            'extra_messages': extra_messages,
        }
        for f, value_nodes in focus_value_nodes.items():
            # we don't use value_nodes in the sparql constraint
            # All queries are done on the corresponding focus node.
            try:
                violations = self.validator.validate(f, value_nodes, target_graph, self.query_helper)
            except ValidationFailure as e:
                raise e
            for val, vio in violations:
                non_conformant = True
                msg_args_map = self.query_helper.param_bind_map.copy()
                msg_args_map.update({"this": f, "value": val})
                if self.shape.is_property_shape:
                    msg_args_map['path'] = self.shape.path()
                self.query_helper.bind_messages(msg_args_map)
                bound_messages = self.query_helper.bound_messages
                # The DASH test suite likes _no_ value entry in the report if we're on a Property Shape.
                report_val = val if not self.shape.is_property_shape else None
                if isinstance(vio, bool):
                    if vio is False:  # ASKValidator Result
                        new_kwargs = rept_kwargs.copy()
                        new_kwargs['extra_messages'].extend(bound_messages)
                        rept = self.make_v_result(target_graph, f, value_node=report_val, **new_kwargs)
                    else:  # SELECTValidator Failure
                        raise ValidationFailure("Validation Failure generated by SPARQLConstraint.")
                elif isinstance(vio, tuple):
                    t, p, v = vio
                    new_msg_args_map = msg_args_map.copy()
                    if v is None:
                        v = report_val
                    else:
                        new_msg_args_map['value'] = v
                    if p is not None:
                        new_msg_args_map['path'] = p
                    if t is not None:
                        new_msg_args_map['this'] = t
                    self.query_helper.bind_messages(new_msg_args_map)
                    new_bound_msgs = self.query_helper.bound_messages
                    new_kwargs = rept_kwargs.copy()
                    new_kwargs['extra_messages'].extend(new_bound_msgs)
                    rept = self.make_v_result(target_graph, t or f, value_node=v, result_path=p, **new_kwargs)
                else:
                    new_kwargs = rept_kwargs.copy()
                    new_kwargs['extra_messages'].extend(bound_messages)
                    rept = self.make_v_result(target_graph, f, value_node=report_val, **new_kwargs)
                reports.append(rept)
        return (not non_conformant), reports


class SPARQLConstraintComponentValidator(object):
    validator_cache: Dict[Tuple[int, str], Union['SelectConstraintValidator', 'AskConstraintValidator']] = {}

    def __new__(cls, shacl_graph: 'ShapesGraph', node, *args, **kwargs):
        cache_key = (id(shacl_graph.graph), str(node))
        found_in_cache = cls.validator_cache.get(cache_key, False)
        if found_in_cache:
            return found_in_cache
        sg = shacl_graph.graph
        type_vals = set(sg.objects(node, RDF_type))
        validator_type: Union[Type[SelectConstraintValidator], Type[AskConstraintValidator], None] = None
        if len(type_vals) > 0:
            if SH_SPARQLSelectValidator in type_vals:
                validator_type = SelectConstraintValidator
            elif SH_SPARQLAskValidator in type_vals:
                validator_type = AskConstraintValidator
        if not validator_type:
            sel_nodes = set(sg.objects(node, SH_select))
            if len(sel_nodes) > 0:
                # TODO:coverage: No test for this case
                validator_type = SelectConstraintValidator
        if not validator_type:
            ask_nodes = set(sg.objects(node, SH_ask))
            if len(ask_nodes) > 0:
                validator_type = AskConstraintValidator

        if not validator_type:
            # TODO:coverage: No test for this case
            raise ConstraintLoadError(
                "Validator must be of type sh:SPARQLSelectValidator or sh:SPARQLAskValidator and must have either a sh:select or a sh:ask predicate.",
                "https://www.w3.org/TR/shacl/#ConstraintComponent",
            )
        validator = validator_type(shacl_graph, node, *args, **kwargs)
        cls.validator_cache[cache_key] = validator
        return validator

    def apply_to_shape_via_constraint(self, constraint, shape, **kwargs) -> BoundShapeValidatorComponent:
        """
        Create a new Custom Constraint (BoundShapeValidatorComponent)
        :param constraint:
        :type constraint: SPARQLConstraintComponent
        :param shape:
        :type shape: pyshacl.shape.Shape
        :param kwargs:
        :return:
        """
        must_be_ask_val = kwargs.pop('must_be_ask_val', False)
        if must_be_ask_val and not (isinstance(self, AskConstraintValidator)):
            # TODO:coverage: No test for this case, do we need to test this?
            raise ConstraintLoadError(
                "Validator not for NodeShape or a PropertyShape must be of type SPARQLAskValidator.",
                "https://www.w3.org/TR/shacl/#ConstraintComponent",
            )
        must_be_select_val = kwargs.pop('must_be_select_val', False)
        if must_be_select_val and not (isinstance(self, SelectConstraintValidator)):
            # TODO:coverage: No test for this case, do we need to test this?
            raise ConstraintLoadError(
                "Validator for a NodeShape or a PropertyShape must be of type SPARQLSelectValidator.",
                "https://www.w3.org/TR/shacl/#ConstraintComponent",
            )

        return BoundShapeValidatorComponent(constraint, shape, self)

    def __init__(self, shacl_graph: 'ShapesGraph', node, **kwargs):
        initialised = getattr(self, 'initialised', False)
        if initialised:
            return
        self.shacl_graph = shacl_graph
        self.node = node
        sg = shacl_graph.graph
        message_nodes = set(sg.objects(node, SH_message))
        for m in message_nodes:
            if not (isinstance(m, rdflib.Literal) and isinstance(m.value, str)):
                # TODO:coverage: No test for when SPARQL-based constraint is RDF Literal is is not of type string
                raise ConstraintLoadError(
                    "Validator sh:message must be an RDF Literal of type xsd:string.",
                    "https://www.w3.org/TR/shacl/#ConstraintComponent",
                )
        self.messages = message_nodes
        self.initialised = True

    def make_messages(self, params_map=None):
        if params_map is None:
            return self.messages
        ret_msgs = []
        for m in self.messages:
            this_m = m.value[:]
            for a, v in params_map.items():
                replace_me = "{$" + str(a) + "}"
                if isinstance(v, rdflib.Literal):
                    v = v.value
                this_m = this_m.replace(replace_me, str(v))
            ret_msgs.append(rdflib.Literal(this_m))
        return ret_msgs


class AskConstraintValidator(SPARQLConstraintComponentValidator):
    def __new__(cls, shacl_graph: 'ShapesGraph', node, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self, shacl_graph: 'ShapesGraph', node, *args, **kwargs):
        super(AskConstraintValidator, self).__init__(shacl_graph, node, **kwargs)
        g = shacl_graph.graph
        ask_vals = set(g.objects(node, SH_ask))
        if len(ask_vals) < 1 or len(ask_vals) > 1:
            # TODO:coverage: No test for this case
            raise ConstraintLoadError(
                "AskValidator must have exactly one value for sh:ask.",
                "https://www.w3.org/TR/shacl/#ConstraintComponent",
            )
        ask_val = next(iter(ask_vals))
        if not (isinstance(ask_val, rdflib.Literal) and isinstance(ask_val.value, str)):
            # TODO:coverage: No test for this case
            raise ConstraintLoadError(
                "AskValidator sh:ask must be an RDF Literal of type xsd:string.",
                "https://www.w3.org/TR/shacl/#ConstraintComponent",
            )
        self.query_text = ask_val.value

    def validate(self, focus, value_nodes, target_graph, query_helper=None, new_bind_vals=None):
        """

        :param focus:
        :param value_nodes:
        :param query_helper:
        :param target_graph:
        :type target_graph: rdflib.Graph
        :param new_bind_vals:
        :return:
        """
        param_bind_vals = query_helper.param_bind_map if query_helper else {}
        new_bind_vals = new_bind_vals or {}
        bind_vals = param_bind_vals.copy()
        bind_vals.update(new_bind_vals)
        violations = set()
        for v in value_nodes:
            if query_helper is None:
                # TODO:coverage: No test for this case when query_helper is None
                init_binds = {}
                sparql_text = self.query_text
            else:
                init_binds, sparql_text = query_helper.pre_bind_variables(
                    focus, valuenode=v, extravars=bind_vals.keys()
                )
                sparql_text = query_helper.apply_prefixes(sparql_text)
                init_binds.update(bind_vals)
            try:
                result = target_graph.query(sparql_text, initBindings=init_binds)
                answer = result.askAnswer
            except (KeyError, AttributeError):
                # TODO:coverage: Can this ever actually happen?
                raise ValidationFailure("ASK Query did not return an askAnswer.")
            if answer is False:
                violations.add((v, False))
        return violations


class SelectConstraintValidator(SPARQLConstraintComponentValidator):
    def __new__(cls, shacl_graph: 'ShapesGraph', node, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self, shacl_graph: 'ShapesGraph', node, *args, **kwargs):
        super(SelectConstraintValidator, self).__init__(shacl_graph, node, **kwargs)
        g = shacl_graph.graph
        select_vals = set(g.objects(node, SH_select))
        if len(select_vals) < 1 or len(select_vals) > 1:
            # TODO:coverage: No test for this case, do we need to test this?
            raise ConstraintLoadError(
                "SelectValidator must have exactly one value for sh:select.",
                "https://www.w3.org/TR/shacl/#ConstraintComponent",
            )
        select_val = next(iter(select_vals))
        if not (isinstance(select_val, rdflib.Literal) and isinstance(select_val.value, str)):
            # TODO:coverage: No test for the case when sh:select is not a literal of type string
            raise ConstraintLoadError(
                "SelectValidator sh:select must be an RDF Literal of type xsd:string.",
                "https://www.w3.org/TR/shacl/#ConstraintComponent",
            )
        self.query_text = select_val.value

    def validate(self, focus, value_nodes, target_graph, query_helper=None, new_bind_vals=None):
        """

        :param focus:
        :param value_nodes:
        :param query_helper:
        :param target_graph:
        :type target_graph: rdflib.Graph
        :param new_bind_vals:
        :return:
        """
        param_bind_vals = query_helper.param_bind_map if query_helper else {}
        new_bind_vals = new_bind_vals or {}
        bind_vals = param_bind_vals.copy()
        bind_vals.update(new_bind_vals)
        violations = set()
        for v in value_nodes:
            if query_helper is None:
                # TODO:coverage: No test for this case when query_helper is None
                init_binds = {}
                sparql_text = self.query_text
            else:
                init_binds, sparql_text = query_helper.pre_bind_variables(
                    focus, valuenode=v, extravars=bind_vals.keys()
                )
                sparql_text = query_helper.apply_prefixes(sparql_text)
                init_binds.update(bind_vals)
            results = target_graph.query(sparql_text, initBindings=init_binds)
            if not results or len(results.bindings) < 1:
                continue
            for r in results:
                try:
                    p = r['path']
                except KeyError:
                    p = None
                try:
                    v2 = r['value']
                except KeyError:
                    v2 = None
                try:
                    t = r['this']
                except KeyError:
                    # TODO:coverage: No test for when result has no 'this' key
                    t = None
                if p or v2 or t:
                    violations.add((v, (t, p, v2)))
                else:
                    # TODO:coverage: No test for generic failure, when
                    #  'path' and 'value' and 'this' are not returned.
                    #  here 'failure' must exist
                    try:
                        f = r['failure']
                        if f is True or (isinstance(f, rdflib.Literal) and f.value):
                            violations.add((v, True))
                    except KeyError:
                        pass
        return violations

class SPARQLConstraintComponent(CustomConstraintComponent):
    """
    SPARQL-based constraints provide a lot of flexibility but may be hard to understand for some people or lead to repetition. This section introduces SPARQL-based constraint components as a way to abstract the complexity of SPARQL and to declare high-level reusable components similar to the Core constraint components. Such constraint components can be declared using the SHACL RDF vocabulary and thus shared and reused.
    Link:
    https://www.w3.org/TR/shacl/#sparql-constraint-components
    """

    __slots__: Tuple = tuple()

    def __new__(cls, shacl_graph, node, parameters, validators, node_validators, property_validators):
        return super(SPARQLConstraintComponent, cls).__new__(
            cls, shacl_graph, node, parameters, validators, node_validators, property_validators
        )

    @property
    def messages(self):
        # TODO: allow messages at this SPARQLConstraintComponent level
        return []

    def make_validator_for_shape(self, shape: 'Shape'):
        """
        :param shape:
        :type shape: Shape
        :return:
        """
        val_count = len(self.validators)
        node_val_count = len(self.node_validators)
        prop_val_count = len(self.property_validators)
        must_be_select_val = False
        must_be_ask_val = False
        if shape.is_property_shape and prop_val_count > 0:
            validator_node = next(iter(self.property_validators))
            must_be_select_val = True
        elif (not shape.is_property_shape) and node_val_count > 0:
            validator_node = next(iter(self.node_validators))
            must_be_select_val = True
        elif val_count > 0:
            validator_node = next(iter(self.validators))
            must_be_ask_val = True
        else:
            raise ConstraintLoadError(
                "Cannot select a validator to use, according to the rules.",
                "https://www.w3.org/TR/shacl/#constraint-components-validators",
            )

        validator = SPARQLConstraintComponentValidator(self.sg, validator_node)
        applied_validator = validator.apply_to_shape_via_constraint(
            self, shape, must_be_ask_val=must_be_ask_val, must_be_select_val=must_be_select_val
        )
        return applied_validator

