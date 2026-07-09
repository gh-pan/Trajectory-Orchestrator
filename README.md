# Trajectory-Orchestrator

A powerful and flexible **trajectory generation and execution toolkit** that repackages the LLM agent trajectory generation system into a universal, harness-agnostic platform. Generate trajectories from seeds, expert knowledge, and demonstrations while seamlessly supporting multiple execution environments.

## 🎯 Overview

Trajectory-Orchestrator transforms trajectory generation from a single-backend system into a **universal platform** that:

- **Generates task trajectories** from multiple sources (seeds, expert knowledge, demonstrations, templates)
- **Supports any execution harness** (LangChain, LangGraph, CrewAI, Autogen, REST APIs, custom implementations)
- **Orchestrates complex workflows** with conditional routing, error handling, and rollback capabilities
- **Captures and analyzes trajectories** with native stream-json format and comprehensive logging
- **Scales from single tasks to enterprise workflows** with flexible deployment options

## ✨ Key Features

### 1. Multi-Source Trajectory Generation

#### Seed-Based Generation
Create trajectories from minimal specifications—just describe what needs to be done, and let the system generate executable steps.

```python
trajectory = maker.from_seed(
    task_description="Search for user data and create a report",
    complexity="medium",
    constraints={"max_steps": 5, "timeout": 300}
)
```

#### Expert Knowledge Integration
Leverage domain expertise and best practices to guide trajectory construction with heuristics and rules.

```python
trajectory = builder.create_from_expertise(
    domain="data_analysis",
    task_type="time_series_forecasting",
    best_practices=["validate_data", "handle_outliers", "cross_validate"],
    constraints={"accuracy_threshold": 0.85}
)
```

#### Demonstration Learning
Extract trajectory patterns from successful executions and demonstrations.

```python
trajectory = maker.from_demonstrations(
    demonstrations=[demo1, demo2, demo3],
    extraction_mode="pattern",
    generalization_level="medium"
)
```

#### Template-Based Generation
Use predefined patterns for common tasks with parameterization.

```python
trajectory = maker.from_template(
    template_name="data_etl_pipeline",
    params={
        "source_format": "csv",
        "target_format": "parquet",
        "transformations": ["deduplicate", "normalize"]
    }
)
```

### 2. Universal Harness Support

Execute the same trajectory definition across different execution environments seamlessly.

#### Pre-built Harness Integrations

| Harness | Status | Use Cases |
|---------|--------|-----------|
| **LangChain** | ✅ Production | LLM chains, RAG, multi-step reasoning |
| **LangGraph** | ✅ Production | State machines, agent graphs, complex workflows |
| **CrewAI** | ✅ Production | Multi-agent systems, role-based execution |
| **Autogen** | ✅ Production | Conversational agents, peer interactions |
| **Python Executor** | ✅ Production | Native Python, direct function calls |
| **REST API** | ✅ Production | Microservices, external systems |
| **Docker Executor** | ✅ Production | Isolated environments, reproducibility |
| **Custom Harness** | ✅ Extensible | Your own execution engine |

#### Unified Execution Interface

```python
from trajectory_orchestrator import TrajectoryExecutor

# Same trajectory, different harnesses
trajectory = maker.from_seed("Process data and generate insights")

# Execute on LangChain
executor_lc = TrajectoryExecutor(harness="langchain")
result_lc = executor_lc.execute(trajectory)

# Execute on CrewAI
executor_crew = TrajectoryExecutor(harness="crewai")
result_crew = executor_crew.execute(trajectory)

# Execute in isolated Docker container
executor_docker = TrajectoryExecutor(harness="docker")
result_docker = executor_docker.execute(trajectory)
```

### 3. Advanced Task Specification

Define complex tasks with flexible step definitions, branching logic, and constraints.

```python
trajectory = (maker
    # Initial processing
    .add_step("load_data", 
        action="load",
        params={"source": "s3://bucket/data.csv"},
        timeout=60
    )
    
    # Conditional branching
    .add_branch(
        condition="data_quality >= 0.8",
        high_path=[
            ("analyze", {"method": "statistical"}),
            ("visualize", {"format": "interactive"})
        ],
        low_path=[
            ("repair", {"strategy": "forward_fill"}),
            ("retry", {"max_attempts": 3})
        ]
    )
    
    # Parallel execution
    .add_parallel([
        ("aggregate", {}),
        ("transform", {}),
        ("validate", {})
    ])
    
    # Error handling
    .add_step("export",
        action="export",
        error_policy={
            "on_failure": "fallback",
            "fallback_action": "store_temp",
            "retry": {"attempts": 3, "backoff": "exponential"}
        }
    )
)
```

### 4. Expert Knowledge System

Build intelligent trajectory generation using domain expertise, heuristics, and learned patterns.

```python
from trajectory_orchestrator import ExpertKnowledgeBuilder

builder = ExpertKnowledgeBuilder()

# Add domain rules
builder.add_rule(
    condition="task_type == 'nlp'",
    action="use_tokenization_step"
)

# Add optimization heuristics
builder.add_heuristic(
    name="data_volume_optimization",
    scoring_fn=lambda data_size: (
        "batch_processing" if data_size > 1e6 else "streaming"
    )
)

# Add best practice patterns
builder.add_pattern(
    name="ml_pipeline",
    steps=[
        "load_data",
        "exploratory_analysis",
        "feature_engineering",
        "model_training",
        "evaluation",
        "deployment"
    ]
)

# Generate trajectory with expertise
trajectory = builder.create_optimized_trajectory(
    task_description="Build ML classifier",
    data_size=5e6,
    latency_requirement="seconds"
)
```

### 5. Execution & Monitoring

Track execution with real-time monitoring, detailed metrics, and automatic error recovery.

```python
from trajectory_orchestrator import TrajectoryExecutor, Monitor

# Setup monitoring
monitor = Monitor(
    enable_profiling=True,
    enable_metrics=True,
    log_level="DEBUG"
)

executor = TrajectoryExecutor(
    harness="langchain",
    monitor=monitor,
    error_handling="adaptive"
)

# Execute with monitoring
results = executor.execute(
    trajectory=trajectory,
    timeout=300,
    on_error="continue"  # Continue on step failures
)

# Get detailed metrics
metrics = monitor.get_metrics()
print(f"Execution time: {metrics['total_time']}s")
print(f"Success rate: {metrics['success_rate']:.1%}")
print(f"Steps executed: {metrics['steps_executed']}")
print(f"Bottlenecks: {metrics['slowest_steps']}")
```

### 6. Trajectory Capture & Analysis

Capture native stream-json trajectories with comprehensive state tracking.

```python
from trajectory_orchestrator import TrajectoryCapture

capture = TrajectoryCapture(format="stream-json")

# Automatic capture during execution
results = executor.execute(trajectory, capture=capture)

# Access raw trajectory
raw_trajectory = capture.get_raw_trajectory()
print(raw_trajectory)  # JSONL format

# Analyze trajectory
analysis = capture.analyze()
print(analysis)
# {
#   "total_steps": 8,
#   "decision_points": 2,
#   "branching_factor": 2,
#   "average_step_duration": 12.5,
#   "error_recovery_count": 1
# }

# Export trajectory
capture.export_to_file("trajectory.jsonl")
capture.export_to_dataset("trajectory_dataset.parquet")
```

## 📦 Installation

### Prerequisites

- Python 3.8+
- pip or uv (recommended)

### Quick Install

```bash
# Using pip
pip install trajectory-orchestrator

# Using uv (faster)
uv pip install trajectory-orchestrator
```

### From Source

```bash
git clone https://github.com/Entropyorder/Trajectory-Orchestrator.git
cd Trajectory-Orchestrator

# Using uv
uv sync

# Or using pip
pip install -e .
```

### Optional Feature Groups

```bash
# LLM integrations (LangChain, OpenAI, Anthropic, etc.)
pip install trajectory-orchestrator[llm]

# Agent frameworks (CrewAI, Autogen, LangGraph)
pip install trajectory-orchestrator[agents]

# Data backends (database, cache, storage)
pip install trajectory-orchestrator[storage]

# Distributed execution
pip install trajectory-orchestrator[distributed]

# Monitoring and observability
pip install trajectory-orchestrator[monitoring]

# All features
pip install trajectory-orchestrator[all]

# Development
pip install trajectory-orchestrator[dev]
```

## 🚀 Quick Start

### Example 1: Basic Seed Generation & Execution

```python
from trajectory_orchestrator import TrajectoryMaker, TrajectoryExecutor

# Create trajectory from seed
maker = TrajectoryMaker()
trajectory = maker.from_seed(
    task_description="Search for Python best practices and summarize in 200 words"
)

# Execute on default harness (LangChain)
executor = TrajectoryExecutor()
results = executor.execute(trajectory)

print(results.output)
```

### Example 2: Expert Knowledge-Driven Generation

```python
from trajectory_orchestrator import ExpertKnowledgeBuilder

# Build with expertise
builder = ExpertKnowledgeBuilder(domain="data_science")
trajectory = builder.create_from_expertise(
    task_type="predictive_analytics",
    dataset_size=1_000_000,
    target_accuracy=0.95,
    constraints={"max_memory": "8GB", "max_time": 3600}
)

# Execute on specified harness
executor = TrajectoryExecutor(harness="crewai")
results = executor.execute(trajectory)
```

### Example 3: Multi-Harness Execution

```python
from trajectory_orchestrator import TrajectoryMaker, TrajectoryExecutor

trajectory = maker.from_seed("Analyze customer sentiment from reviews")

# Execute across multiple harnesses
harnesses = ["langchain", "crewai", "autogen"]
results = {}

for harness_name in harnesses:
    executor = TrajectoryExecutor(harness=harness_name)
    results[harness_name] = executor.execute(trajectory)
    
# Compare results
for harness, result in results.items():
    print(f"{harness}: {result.metrics['quality_score']}")
```

### Example 4: Complex Trajectory with Branching

```python
from trajectory_orchestrator import TrajectoryMaker

maker = TrajectoryMaker()
trajectory = (maker
    .add_step("receive_request")
    .add_step("validate_input")
    .add_branch(
        condition="input_score > 0.8",
        then_steps=[
            ("fast_process", {"method": "cached"}),
            ("return_result", {})
        ],
        else_steps=[
            ("detailed_analysis", {"depth": "full"}),
            ("enrich_data", {}),
            ("return_result", {})
        ]
    )
    .build()
)

executor = TrajectoryExecutor(harness="langchain")
results = executor.execute(trajectory)
```

### Example 5: Capture & Analyze Trajectories

```python
from trajectory_orchestrator import TrajectoryExecutor, TrajectoryCapture

trajectory = maker.from_seed("Complex multi-step task")

# Execute with capture
capture = TrajectoryCapture()
executor = TrajectoryExecutor(capture=capture)
results = executor.execute(trajectory)

# Analyze
analysis = capture.analyze()
print(f"Decision complexity: {analysis['decision_tree_depth']}")
print(f"Branch coverage: {analysis['branching_factor']}")
print(f"Performance: {analysis['metrics']}")

# Export for reuse
capture.export_to_dataset("trajectories.parquet")
```

## 📚 Core Concepts

### Trajectory

A structured workflow definition consisting of steps, branches, and metadata:

```python
{
    "id": "traj_001",
    "name": "Data Analysis Pipeline",
    "description": "Load, validate, and analyze dataset",
    "version": "1.0.0",
    "steps": [
        {
            "id": "step_1",
            "type": "action",
            "action": "load_data",
            "params": {"source": "s3://bucket/data.csv"},
            "timeout": 60,
            "retry": {"max_attempts": 3, "backoff": "exponential"}
        },
        {
            "id": "step_2",
            "type": "conditional",
            "condition": "data_rows > 1000",
            "then_steps": ["step_3a"],
            "else_steps": ["step_3b"]
        }
    ],
    "metadata": {
        "author": "data_team",
        "created_at": "2026-07-09",
        "tags": ["analytics", "production"],
        "estimated_duration": 300,
        "supported_harnesses": ["langchain", "crewai"]
    }
}
```

### Harness

An execution adapter implementing the `BaseHarness` interface:

```python
from trajectory_orchestrator.harness import BaseHarness

class CustomHarness(BaseHarness):
    def __init__(self, config=None):
        super().__init__(config)
        self.executor = ...  # Your execution engine
    
    def execute_step(self, step, context):
        """Execute a single step."""
        pass
    
    def execute(self, trajectory, context=None):
        """Execute full trajectory."""
        pass
    
    def validate(self, trajectory):
        """Validate trajectory compatibility."""
        pass
    
    def get_state(self):
        """Return current execution state."""
        pass
```

### Trajectory Maker

Factory for generating trajectories from various sources:

```python
maker = TrajectoryMaker()

# Method 1: From seed description
t1 = maker.from_seed(description, complexity, constraints)

# Method 2: From expert knowledge
t2 = maker.from_knowledge(domain, task_type, expertise_config)

# Method 3: From demonstrations
t3 = maker.from_demonstrations(demo_list, extraction_mode)

# Method 4: From template
t4 = maker.from_template(template_name, parameters)

# Method 5: From composition
t5 = maker.compose(trajectory_list, mode="sequential")
```

### Expert Knowledge Builder

Intelligent trajectory generation using domain expertise:

```python
builder = ExpertKnowledgeBuilder(domain="machine_learning")
builder.add_rule(...)
builder.add_heuristic(...)
builder.add_pattern(...)
trajectory = builder.create_optimized_trajectory(...)
```

## 🔧 Configuration

### Environment Configuration

Create `.env` file or set environment variables:

```bash
# Default execution harness
TRAJECTORY_DEFAULT_HARNESS=langchain

# Execution parameters
TRAJECTORY_DEFAULT_TIMEOUT=300
TRAJECTORY_MAX_RETRIES=3
TRAJECTORY_RETRY_BACKOFF=exponential

# Logging
TRAJECTORY_LOG_LEVEL=INFO
TRAJECTORY_LOG_FILE=trajectory.log
TRAJECTORY_LOG_FORMAT=json

# Monitoring
TRAJECTORY_ENABLE_MONITORING=true
TRAJECTORY_ENABLE_PROFILING=false
TRAJECTORY_METRICS_BACKEND=prometheus

# Database (optional)
TRAJECTORY_DB_URL=postgresql://user:pass@localhost/trajectories
TRAJECTORY_CACHE_BACKEND=redis://localhost:6379

# Security
TRAJECTORY_SANITIZE_SECRETS=true
TRAJECTORY_SANITIZE_PATHS=true
```

### Programmatic Configuration

```python
from trajectory_orchestrator import Config

config = Config()
config.set_default_harness("langchain")
config.set_timeout(300)
config.set_log_level("DEBUG")
config.enable_monitoring(True)
config.set_cache_backend("redis")

# Apply globally
Config.apply_global(config)
```

### Per-Execution Configuration

```python
executor = TrajectoryExecutor(
    harness="langchain",
    timeout=600,
    retry_policy={"max_attempts": 5, "backoff": "exponential"},
    error_handling="adaptive",
    monitoring={"enabled": True, "verbose": True}
)
```

## 🎨 Advanced Features

### Parallel Execution

```python
trajectory = maker.from_seed(
    description="Multi-task processing",
    execution_mode="parallel",
    max_parallel_steps=4
)

# Or explicit parallel blocks
trajectory = (maker
    .add_step("prepare_data")
    .add_parallel([
        ("process_stream_a", {}),
        ("process_stream_b", {}),
        ("process_stream_c", {})
    ])
    .add_step("aggregate_results")
)
```

### Complex Branching & Conditional Logic

```python
trajectory = (maker
    .add_step("assess_input")
    .add_branch(
        condition="input_quality >= 0.8 and data_size < 1e6",
        then_steps=[
            ("fast_path", {"method": "direct"}),
            ("direct_result", {})
        ],
        else_steps=[
            ("slow_path", {"method": "comprehensive"}),
            ("enriched_result", {})
        ]
    )
    .add_switch([
        (lambda ctx: ctx.method == "A", [step_a1, step_a2]),
        (lambda ctx: ctx.method == "B", [step_b1, step_b2]),
    ])
)
```

### Error Handling & Resilience

```python
trajectory = (maker
    .add_step("risky_operation",
        retry_policy={
            "max_attempts": 5,
            "backoff_strategy": "exponential",
            "backoff_base": 2,
            "jitter": True,
            "timeout": 60
        },
        error_handling={
            "on_failure": "continue",  # or "stop", "fallback"
            "fallback_step": "use_cached_data",
            "notify": ["alerts@company.com"]
        }
    )
)
```

### Dynamic Parameter Substitution

```python
trajectory = maker.from_seed(
    description="Process user data",
    parameters={
        "user_id": "${INPUT.user_id}",  # From input
        "timestamp": "${NOW}",           # Current time
        "config": "${ENV.DATA_CONFIG}"   # From environment
    }
)

results = executor.execute(
    trajectory,
    input={"user_id": "user_123"},
    env={"DATA_CONFIG": "/etc/config.yaml"}
)
```

### Trajectory Composition

```python
# Combine multiple trajectories
trajectory = maker.compose(
    trajectories=[
        validate_traj,
        process_traj,
        export_traj
    ],
    composition_mode="sequential",  # or "parallel", "conditional"
    error_handling="stop_on_first"
)
```

### Caching & Memoization

```python
executor = TrajectoryExecutor(
    harness="langchain",
    cache={
        "enabled": True,
        "backend": "redis",
        "ttl": 3600,
        "key_fn": lambda step: f"{step['id']}:{hash(step['params'])}"
    }
)

results = executor.execute(trajectory)  # Cached for 1 hour
```

## 📊 Monitoring & Observability

### Real-time Monitoring

```python
from trajectory_orchestrator import Monitor

monitor = Monitor(
    metrics_backend="prometheus",
    trace_backend="jaeger",
    log_backend="loki"
)

executor = TrajectoryExecutor(harness="langchain", monitor=monitor)
results = executor.execute(trajectory)

# Get detailed metrics
metrics = monitor.get_metrics()
# {
#   "total_duration": 45.2,
#   "steps_executed": 5,
#   "success_rate": 0.95,
#   "error_rate": 0.05,
#   "avg_step_duration": 9.04,
#   "bottleneck_steps": ["step_2", "step_4"],
#   "resource_usage": {...}
# }
```

### Detailed Logging

```python
from trajectory_orchestrator import Logger

logger = Logger(
    level="DEBUG",
    format="json",
    outputs=["console", "file://trajectory.log", "stdout://metrics"]
)

executor = TrajectoryExecutor(harness="langchain", logger=logger)
results = executor.execute(trajectory)

# Logs contain:
# - Full trajectory definition
# - Each step execution details
# - Decision points and branching
# - Performance metrics
# - Error traces
```

### Custom Observers

```python
from trajectory_orchestrator.observer import BaseObserver

class CustomObserver(BaseObserver):
    def on_trajectory_start(self, trajectory):
        pass
    
    def on_step_start(self, step):
        pass
    
    def on_step_complete(self, step, result):
        pass
    
    def on_trajectory_complete(self, trajectory, results):
        pass

executor = TrajectoryExecutor(observers=[CustomObserver()])
```

## 🧪 Testing & Validation

### Trajectory Validation

```python
from trajectory_orchestrator import TrajectoryValidator

validator = TrajectoryValidator()
is_valid, errors, warnings = validator.validate(trajectory)

if not is_valid:
    for error in errors:
        print(f"Error: {error}")

for warning in warnings:
    print(f"Warning: {warning}")
```

### Unit Testing Trajectories

```python
import unittest
from trajectory_orchestrator import TrajectoryMaker, TrajectoryExecutor

class TestMyTrajectories(unittest.TestCase):
    def setUp(self):
        self.maker = TrajectoryMaker()
        self.executor = TrajectoryExecutor(harness="langchain")
    
    def test_basic_trajectory(self):
        trajectory = self.maker.from_seed("Simple task")
        results = self.executor.execute(trajectory)
        self.assertIsNotNone(results)
        self.assertEqual(results.status, "success")
    
    def test_error_handling(self):
        trajectory = self.maker.from_seed("Task with errors")
        results = self.executor.execute(trajectory)
        # Verify error was handled gracefully
        self.assertIn("recovered", results.metadata)
```

### Integration Testing

```python
@pytest.mark.integration
def test_multi_harness_execution():
    trajectory = maker.from_seed("Test task")
    
    for harness in ["langchain", "crewai"]:
        executor = TrajectoryExecutor(harness=harness)
        results = executor.execute(trajectory)
        assert results.status == "success"
```

### End-to-End Testing

```python
@pytest.mark.e2e
def test_full_workflow():
    # Generate trajectory
    trajectory = builder.create_from_expertise(
        domain="data_analysis",
        task_type="reporting"
    )
    
    # Execute
    executor = TrajectoryExecutor(harness="langchain")
    results = executor.execute(trajectory)
    
    # Verify results
    assert len(results.output) > 0
    assert results.metrics['success_rate'] > 0.95
```

## 🔌 Building Custom Harnesses

### Step 1: Inherit BaseHarness

```python
from trajectory_orchestrator.harness import BaseHarness

class MyCustomHarness(BaseHarness):
    def __init__(self, config=None):
        super().__init__(config)
        self.setup_executor()
    
    def setup_executor(self):
        # Initialize your execution engine
        pass
```

### Step 2: Implement Required Methods

```python
def execute_step(self, step, context):
    """Execute a single step in your execution engine."""
    action = step.get('action')
    params = step.get('params', {})
    
    # Execute your logic
    result = self.executor.run(action, params)
    
    return {
        'status': 'success',
        'output': result,
        'duration': execution_time
    }

def execute(self, trajectory, context=None):
    """Execute the full trajectory."""
    results = []
    for step in trajectory['steps']:
        result = self.execute_step(step, context)
        results.append(result)
        if result['status'] == 'failure':
            break
    
    return results

def validate(self, trajectory):
    """Validate trajectory is compatible with your harness."""
    for step in trajectory['steps']:
        if step['action'] not in self.supported_actions:
            return False
    return True
```

### Step 3: Register & Use

```python
from trajectory_orchestrator import TrajectoryExecutor

# Register custom harness
executor = TrajectoryExecutor(harness=MyCustomHarness())

# Use as normal
results = executor.execute(trajectory)
```

## 📖 API Reference

### TrajectoryMaker

```python
class TrajectoryMaker:
    # Generation methods
    def from_seed(description, complexity=None, constraints=None) -> Trajectory
    def from_knowledge(domain, task_type, constraints=None) -> Trajectory
    def from_demonstrations(demos, extraction_mode="pattern") -> Trajectory
    def from_template(name, parameters) -> Trajectory
    
    # Building methods
    def add_step(name, action, params=None, **kwargs) -> Builder
    def add_branch(condition, then_steps, else_steps) -> Builder
    def add_parallel(steps) -> Builder
    def add_error_handling(strategy, **config) -> Builder
    
    # Finalization
    def build() -> Trajectory
    def compose(trajectories, mode) -> Trajectory
```

### TrajectoryExecutor

```python
class TrajectoryExecutor:
    def __init__(harness, config=None, monitor=None, logger=None)
    def execute(trajectory, input=None, env=None) -> ExecutionResult
    def validate(trajectory) -> ValidationResult
    def estimate(trajectory) -> Estimate
    def explain(trajectory) -> Explanation
```

### ExpertKnowledgeBuilder

```python
class ExpertKnowledgeBuilder:
    def __init__(domain=None)
    def add_rule(condition, action) -> Self
    def add_heuristic(name, scoring_fn) -> Self
    def add_pattern(name, steps) -> Self
    def create_optimized_trajectory(**params) -> Trajectory
```

### Monitor

```python
class Monitor:
    def get_metrics() -> Metrics
    def get_trace() -> Trace
    def export(format="json") -> str
```

## 🌍 Common Use Cases

### 1. Data Processing Pipelines

```python
trajectory = builder.create_from_expertise(
    domain="data_engineering",
    task_type="etl_pipeline",
    data_volume="large",
    latency_requirement="batch"
)

executor = TrajectoryExecutor(harness="langchain")
results = executor.execute(trajectory, input={"source": "s3://data"})
```

### 2. Multi-Agent Systems

```python
trajectory = maker.from_seed(
    description="Coordinate multiple agents for code review",
    execution_mode="parallel"
)

executor = TrajectoryExecutor(harness="crewai")
results = executor.execute(trajectory)
```

### 3. LLM Agent Workflows

```python
trajectory = maker.from_template(
    name="llm_reasoning_chain",
    parameters={"reasoning_depth": 3, "verification": True}
)

executor = TrajectoryExecutor(harness="langchain")
results = executor.execute(trajectory)
```

### 4. Automated Testing

```python
trajectory = builder.create_from_expertise(
    domain="testing",
    task_type="integration_test_generation"
)

executor = TrajectoryExecutor(harness="custom_test_harness")
results = executor.execute(trajectory)
```

### 5. DevOps & Infrastructure Automation

```python
trajectory = maker.from_seed(
    description="Deploy service with health checks and rollback on failure"
)

executor = TrajectoryExecutor(harness="custom_devops_harness")
results = executor.execute(trajectory)
```

## 📦 Command-Line Interface

```bash
# Generate trajectory from seed
trajectory-maker generate \
  --description "Process customer data" \
  --harness langchain \
  --output trajectory.yaml

# Validate trajectory
trajectory-maker validate trajectory.yaml \
  --harness crewai

# Execute trajectory
trajectory-maker execute trajectory.yaml \
  --harness langchain \
  --output results.json

# End-to-end: generate, validate, execute
trajectory-maker run \
  --description "My task" \
  --harness langchain \
  --output results/

# Analyze trajectory performance
trajectory-maker analyze results.json

# Clean up
trajectory-maker cleanup --all
```

## 🤝 Contributing

We welcome contributions! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Setup

```bash
uv sync
uv run pytest
```

## 📝 License

This project is licensed under the MIT License - see [LICENSE](LICENSE) for details.

## 🙏 Acknowledgments

- Inspired by workflow orchestration and agent execution best practices
- Built with support for the LLM and AI agent community
- Thanks to all contributors

## 📮 Support & Resources

- **Documentation**: [Full Docs](https://trajectory-orchestrator.readthedocs.io/)
- **GitHub Issues**: [Report Issues](https://github.com/Entropyorder/Trajectory-Orchestrator/issues)
- **Discussions**: [Community Discussions](https://github.com/Entropyorder/Trajectory-Orchestrator/discussions)
- **Email**: support@entropyorder.com

## 🗺️ Roadmap

- [ ] Web UI for interactive trajectory composition
- [ ] Advanced visualization and debugging tools
- [ ] Trajectory optimization and auto-tuning engine
- [ ] Distributed and federated execution support
- [ ] GraphQL API for trajectory queries
- [ ] Community trajectory marketplace
- [ ] Performance benchmarking suite
- [ ] Real-time collaboration features

---

**Trajectory-Orchestrator** — *Generate, orchestrate, and execute task trajectories across any harness with confidence* ✨
