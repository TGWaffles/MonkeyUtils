import humanize
import pandas
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import make_interp_spline
import matplotlib.dates as dates

from io import BytesIO

matplotlib.use("Agg")


def file_from_timestamps(times, group):
    file = BytesIO()
    series = pandas.Series(times)
    series.index = series.dt.to_period(group)
    series = series.groupby(level=0).size()
    series = series.reindex(pandas.period_range(series.index.min(), series.index.max(), freq=group), fill_value=0)
    bar_chart = series.plot(subplots=False)
    bar_chart.spines['bottom'].set_position('zero')
    figure = bar_chart.get_figure()
    figure.tight_layout()
    figure.savefig(file)
    file.seek(0)
    return file.read()


def pie_chart_from_amount_and_labels(labels, amounts):
    file = BytesIO()
    amounts = np.array(amounts)
    fig = plt.figure()
    axes = fig.add_axes([0, 0, 1, 1])
    axes.axis("equal")
    axes.pie(amounts, labels=labels, autopct='%1.1f%%')
    fig.savefig(file)
    file.seek(0)
    return file.read()


def num_humanizer(x, pos=0):
    return humanize.intword(x, format="%.2f")


def plot_multiple(x_label="", y_label="", title="", **kwargs):
    file = BytesIO()
    plt.gca().xaxis.set_major_formatter(dates.DateFormatter("%Y-%m-%d %H:%M"))
    plt.gca().yaxis.set_major_formatter(num_humanizer)
    interval = max([1] + [len(x) // 10 for x in kwargs.values()])
    print(f"{kwargs.values()}")
    print(interval)
    plt.gca().xaxis.set_major_locator(dates.HourLocator(interval=interval))
    for kwarg_title, data in kwargs.items():
        x = [x[0] for x in data]
        y = [x[1] for x in data]
        plt.plot(x, y, label=kwarg_title)
    plt.gcf().autofmt_xdate()
    plt.xlabel(x_label)
    plt.ylabel(y_label)
    plt.title(title)
    plt.legend()
    plt.tight_layout()
    plt.savefig(file)
    file.seek(0)
    return file.read()


def plot_stats(data, *_, x_label=None, y_label=None):
    file = BytesIO()
    x_values = np.arange(-len(data) + 1, 1, 1)
    if len(data) < 4:
        x_new = x_values
        y_smooth = data
    else:
        x_new = np.linspace(min(x_values), max(x_values), len(x_values) * 100)
        spline = make_interp_spline(x_values, data, k=2)
        y_smooth = spline(x_new)
    plt.plot(x_new, y_smooth)
    if x_label is not None:
        plt.xlabel(x_label)
    if y_label is not None:
        plt.ylabel(y_label)
    if len(x_values) < 10:
        plt.xticks(x_values)
    plt.grid()
    plt.savefig(file)
    file.seek(0)
    return file.read()


def plot_and_extrapolate(input_data, extrapolated_values, *_, x_label=None, y_label=None):
    file = BytesIO()
    x_values = np.arange(-len(input_data) + 1, 1, 1)
    extrapolate_max = int(round(0.5 * len(input_data)))
    new_values = np.arange(-len(input_data) + 1, extrapolate_max, 1)
    if len(input_data) < 4:
        x_new = x_values
        y_smooth = input_data
    else:
        x_new = np.linspace(min(x_values), max(x_values), len(x_values) * 100)
        spline = make_interp_spline(x_values, input_data, k=2)
        y_smooth = spline(x_new)
    plt.plot(x_new, y_smooth, 'b-', label='True Data')
    plt.plot(new_values, extrapolated_values, 'r--', label="Extrapolated Data")
    if x_label is not None:
        plt.xlabel(x_label)
    if y_label is not None:
        plt.ylabel(y_label)
    if len(new_values) < 10:
        plt.xticks(new_values)
    plt.grid()
    plt.savefig(file)
    file.seek(0)
    return file.read()
