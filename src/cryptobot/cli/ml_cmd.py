"""CLI: 机器学习命令"""

import json

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group()
def ml():
    """机器学习"""
    pass


@ml.command("train")
@click.option("--days", default=180, help="训练数据天数")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def train(days: int, json_output: bool):
    """训练 LightGBM 信号评分模型"""
    from datetime import datetime, timezone

    from cryptobot.ml.lgb_scorer import (
        prepare_training_data,
        save_model,
        train_model,
    )

    X, y = prepare_training_data(days=days)
    if not X:
        console.print("[yellow]无可用训练数据[/yellow]")
        return

    console.print(f"[cyan]训练数据: {len(X)} 样本[/cyan]")

    model, metrics = train_model(X, y)

    version = datetime.now(tz=timezone.utc).strftime("v%Y%m%d_%H%M%S")
    path = save_model(model, version)

    result = {
        "version": version,
        "samples": len(X),
        "positive_rate": round(sum(y) / len(y), 4),
        "metrics": {
            "accuracy": metrics.accuracy,
            "auc_roc": metrics.auc_roc,
            "precision": metrics.precision,
            "recall": metrics.recall,
            "f1": metrics.f1,
        },
        "model_path": path,
        "top_features": dict(
            sorted(
                metrics.feature_importance.items(),
                key=lambda x: -x[1],
            )[:10]
        ),
    }

    if json_output:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    console.print(f"\n[bold]模型训练完成: {version}[/bold]\n")

    table = Table(title="评估指标")
    table.add_column("指标", style="cyan")
    table.add_column("值", justify="right")
    table.add_row("样本数", str(len(X)))
    table.add_row("正例比例", f"{result['positive_rate']:.1%}")
    table.add_row("Accuracy", f"{metrics.accuracy:.4f}")
    table.add_row("AUC-ROC", f"{metrics.auc_roc:.4f}")
    table.add_row("Precision", f"{metrics.precision:.4f}")
    table.add_row("Recall", f"{metrics.recall:.4f}")
    table.add_row("F1", f"{metrics.f1:.4f}")
    console.print(table)

    if result["top_features"]:
        ft = Table(title="Top 特征重要性")
        ft.add_column("特征", style="cyan")
        ft.add_column("重要性", justify="right")
        for name, imp in result["top_features"].items():
            ft.add_row(name, f"{imp:.2f}")
        console.print(ft)

    console.print(f"\n模型已保存: {path}")


@ml.command("score")
@click.option("--symbol", required=True, help="交易对")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def score(symbol: str, json_output: bool):
    """对指定币种进行信号评分"""
    from cryptobot.features.feature_store import load_latest_features
    from cryptobot.ml.lgb_scorer import score_signal

    matrix = load_latest_features()
    if matrix is None:
        console.print("[yellow]无可用特征数据[/yellow]")
        return

    # 查找对应币种的特征向量
    target = None
    for vec in matrix.vectors:
        if vec.symbol == symbol:
            target = vec
            break

    if target is None:
        console.print(f"[yellow]未找到 {symbol} 的特征数据[/yellow]")
        return

    try:
        result = score_signal(symbol, target.features)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return

    output = {
        "symbol": result.symbol,
        "direction": result.direction,
        "probability": result.probability,
        "model_version": result.model_version,
        "features_used": result.features_used,
    }

    if json_output:
        click.echo(json.dumps(output, indent=2, ensure_ascii=False))
        return

    console.print(f"\n[bold]信号评分: {symbol}[/bold]\n")

    table = Table()
    table.add_column("项目", style="cyan")
    table.add_column("值", justify="right")
    table.add_row("方向", result.direction)
    table.add_row("概率", f"{result.probability:.4f}")
    table.add_row("模型版本", result.model_version)
    table.add_row("特征数", str(result.features_used))
    console.print(table)


@ml.command("evaluate")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def evaluate(json_output: bool):
    """评估当前模型的各项指标"""
    from cryptobot.ml.lgb_scorer import load_latest_model, prepare_training_data

    try:
        model, version = load_latest_model()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return

    X, y = prepare_training_data(days=90)
    if not X:
        console.print("[yellow]无可用评估数据[/yellow]")
        return

    # 获取特征名
    feature_names = model.feature_name()

    # 预测
    X_matrix = [[d.get(name, 0.0) for name in feature_names] for d in X]
    probs = model.predict(X_matrix)
    preds = [1 if p > 0.5 else 0 for p in probs]

    from cryptobot.ml.lgb_scorer import _compute_metrics

    imp = model.feature_importance(importance_type="gain")
    metrics = _compute_metrics(y, preds, list(probs), feature_names, list(imp))

    result = {
        "model_version": version,
        "eval_samples": len(X),
        "metrics": {
            "accuracy": metrics.accuracy,
            "auc_roc": metrics.auc_roc,
            "precision": metrics.precision,
            "recall": metrics.recall,
            "f1": metrics.f1,
        },
    }

    if json_output:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    console.print(f"\n[bold]模型评估: {version}[/bold]\n")

    table = Table(title=f"评估指标 ({len(X)} 样本)")
    table.add_column("指标", style="cyan")
    table.add_column("值", justify="right")
    table.add_row("Accuracy", f"{metrics.accuracy:.4f}")
    table.add_row("AUC-ROC", f"{metrics.auc_roc:.4f}")
    table.add_row("Precision", f"{metrics.precision:.4f}")
    table.add_row("Recall", f"{metrics.recall:.4f}")
    table.add_row("F1", f"{metrics.f1:.4f}")
    console.print(table)
