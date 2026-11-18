# Third-party notices

## TheAgentCompany

The evaluation tasks in `src/wjfyp/eval/tasks/` are ported from TheAgentCompany benchmark
(https://github.com/TheAgentCompany/TheAgentCompany). Their checkpoint logic is adapted from
the upstream `evaluator.py` files of these tasks.

- `sde_fix_factual_mistake.py` from `sde-fix-factual-mistake`
- `sde_write_a_unit_test.py` from `sde-write-a-unit-test-for-append_file-function`
- `sde_write_a_unit_test_strict.py`, a corrected rubric for the same task, built on the port above

TheAgentCompany is distributed under the MIT License, reproduced below.

```text
MIT License

Copyright (c) 2024 TheAgentCompany

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
