import re

import dspy
from dspy.signatures.signature import ensure_signature

from ..primitives.program import Module
from ..primitives.ipython_interpreter import JupyterKernelInterpreter

class KernelOfThought(Module):
    def __init__(self, signature, max_iters=3, import_white_list=None):
        super().__init__()
        self.signature = signature = ensure_signature(signature)
        self.signature = self.signature.prepend(
            "defined_variables",
            dspy.InputField(prefix="Defined Variables:",
            desc="list of previously defined variables in the IPynb environment",
            format=str,)
        )

        self.max_iters = max_iters
        self.import_white_list = import_white_list
        self.input_fields = self.signature.input_fields
        print(f"Printing input fields {self.input_fields}")
        self.output_fields = self.signature.output_fields

        self.variables = []

        assert len(self.output_fields) == 1, "IT only supports one output field."
        self.output_field_name = next(iter(self.output_fields))

        self.interpreter = JupyterKernelInterpreter(False)
        self.interpreter._initialize_if_needed()
        inputs_ = ", ".join(
            [f"`{field_name}`" for field_name in self.input_fields.keys()],
        )
        outputs_ = f"`{self.output_field_name}`"

        self.code_generate = dspy.ChainOfThought(
            dspy.Signature(
                self._generate_signature("generate").fields,
                self._generate_instruction("generate"),
            ),
        )
        self.code_regenerate = dspy.ChainOfThought(
            dspy.Signature(
                self._generate_signature("regenerate").fields,
                self._generate_instruction("regenerate"),
            ),
        )
    
    def _generate_signature(self, mode):
        signature_dict = dict(self.input_fields)
        fields_for_mode = {
            "generate": {
                "defined_variables": dspy.InputField(
                    prefix="Defined Variables:",
                    desc="list of previously defined variables in the IPynb environment",
                    format=str,
                ),
                "generated_code": dspy.OutputField(
                    prefix="Code:",
                    desc="python code that answers the question",
                    format=str,
                ),
            },
            "regenerate": {
                "defined_variables": dspy.InputField(
                    prefix="Defined Variables:",
                    desc="list of previously defined variables in the IPynb environment",
                    format=str,
                ),
                "error": dspy.InputField(
                    prefix="Error:",
                    desc="error message from previously-generated python code",
                ),
                "generated_code": dspy.OutputField(
                    prefix="Code:",
                    desc="python code that answers the question",
                    format=str,
                ),
            },
        }
        signature_dict.update(fields_for_mode[mode])
        return dspy.Signature(signature_dict)

    def _generate_instruction(self, mode):
        #TODO Fix Regen Instructions
        mode_inputs = ", ".join(
            [
                f"`{field_name}`"
                for field_name in self._generate_signature(mode).input_fields
            ],
        )
        mode_outputs = f"`{self.output_field_name}`"
        if mode == "generate":
            instr = [
                f"You will be given {mode_inputs} and you will respond with {mode_outputs}.",
                f"Generate executable Python code that programmatically computes the correct {mode_outputs}.", 
                f"Use variable names that are clear, meaningful, and convey their specific purpose and content.",
                f"After you're done with the computation, make sure the last line in your code evaluates to the correct value for {mode_outputs}.",
            ]
        elif mode == "regenerate":
            instr = [
                f"You are given {mode_inputs} due to an error in previous code.",
                f"Use clear and meaningful variable names that accurately represent their purpose or content.", 
                f"Use variable names that are clear, meaningful, and convey their specific purpose and content.",
                f"Your task is to correct the error and provide the new {mode_outputs}.",
            ]
        return "\n".join(instr)
    
    def parse_code(self, code_data):
        code = (
            code_data.get("generated_code", "").split("---", 1)[0].split("\n\n\n", 1)[0]
        )
        code_match = re.search(r"```python[ \n](.*?)[ \n]```?", code, re.DOTALL)
        code_block = (code_match.group(1) if code_match else code).replace("\\n", "\n")
        if not code_block:
            return code, "Error: Empty code after parsing."
        if "\n" not in code_block and code_block.count("=") > 1:
            return code, "Error: Code format is not correct."
        lines = code_block.split("\n")
        last_line_match = re.match(r"^(\w+)\s*=", lines[-1].strip())
        if last_line_match and len(lines) > 1:
            code_block += "\n" + last_line_match.group(1)
        else:
            code_block = re.sub(
                r"([a-zA-Z_]\w* *=.*?)(?=[a-zA-Z_]\w* *=)", r"\1\n", code_block,
            )
            code_block = re.sub(
                r"([a-zA-Z_]\w* *=.*?)([a-zA-Z_]\w*)$", r"\1\n\2", code_block,
            )
        return code_block, None
    
    def execute_code(self, code):
        if not code:
            return code, None, "Error: Empty code before execution."
        interpreter = self.interpreter
        try:
            output = str(interpreter._execute(code, timeout=30))
            print(output)
            print("in try")
            return code, output, None
        except Exception as e:
            print("In exception")
            return code, None, str(e)
        
    def forward(self, **kwargs):
        input_kwargs = {
             field_name: kwargs[field_name] for field_name in self.input_fields
        }
        # TODO: remove me later im temporary
        #input_kwargs.update({"defined_variables": kwargs['defined_variables']})
        # print(f'kwargs are as follows: {kwargs}')
        # print(f'input named are as follows: {self.input_fields}')
        code_data = self.code_generate(**input_kwargs)
        parsed_code, error = self.parse_code(code_data)
        print(parsed_code)
        # FIXME: Don't try to execute the code if it didn't parse
        code, output, error = self.execute_code(parsed_code)
        hop = 0
        while hop < self.max_iters and error:
            print("Error in code execution")
            input_kwargs.update({"previous_code": code, "error": error})
            print(error)
            code_data = self.code_regenerate(**input_kwargs)
            parsed_code, error = self.parse_code(code_data)
            # FIXME: Don't try to execute the code if it didn't parse
            code, output, error = self.execute_code(parsed_code)
            print(error)

            print(code)
            print(output)

            hop += 1
            if hop == self.max_iters:
                print("Max hops reached. Error persists.")
                return None
        return output
