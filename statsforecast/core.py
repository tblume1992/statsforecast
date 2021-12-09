# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/core.ipynb (unless otherwise specified).

__all__ = ['StatsForecast']

# Cell
import inspect
import logging
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

# Internal Cell
logging.basicConfig(
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Internal Cell
class GroupedArray:

    def __init__(self, data, indptr):
        self.data = data
        self.indptr = indptr
        self.n_groups = self.indptr.size - 1

    def __getitem__(self, idx):
        if isinstance(idx, int):
            return self.data[self.indptr[idx] : self.indptr[idx + 1]]
        elif isinstance(idx, slice):
            idx = slice(idx.start, idx.stop + 1, idx.step)
            new_indptr = self.indptr[idx].copy()
            new_data = self.data[new_indptr[0] : new_indptr[-1]].copy()
            new_indptr -= new_indptr[0]
            return GroupedArray(new_data, new_indptr)
        raise ValueError(f'idx must be either int or slice, got {type(idx)}')

    def __len__(self):
        return self.n_groups

    def __repr__(self):
        return f'GroupedArray(n_data={self.data.size:,}, n_groups={self.n_groups:,})'

    def __eq__(self, other):
        if not hasattr(other, 'data') or not hasattr(other, 'indptr'):
            return False
        return np.allclose(self.data, other.data) and np.array_equal(self.indptr, other.indptr)

    def compute_forecasts(self, h, func, *args):
        out = np.full(h * self.n_groups, np.nan, dtype=np.float32)
        for i, grp in enumerate(self):
            out[h * i : h * (i + 1)] = func(grp, h, *args)
        return out

    def split(self, n_chunks):
        return [self[x[0] : x[-1] + 1] for x in np.array_split(range(self.n_groups), n_chunks)]

# Internal Cell
def _grouped_array_from_df(df):
    df = df.set_index('ds', append=True)
    if not df.index.is_monotonic_increasing:
        df = df.sort_index()
    data = df['y'].values.astype(np.float32)
    indices_sizes = df.index.get_level_values('unique_id').value_counts(sort=False)
    indices = indices_sizes.index
    sizes = indices_sizes.values
    cum_sizes = sizes.cumsum()
    dates = df.index.get_level_values('ds')[cum_sizes - 1]
    indptr = np.append(0, cum_sizes).astype(np.int32)
    return GroupedArray(data, indptr), indices, dates

# Internal Cell
def _build_forecast_name(model, *args) -> str:
    model_name = f'{model.__name__}'
    func_params = inspect.signature(model).parameters
    func_args = list(func_params.items())[2:]  # remove input array and horizon
    changed_params = [
        f'{name}-{value}'
        for value, (name, arg) in zip(args, func_args)
        if arg.default != value
    ]
    if changed_params:
        model_name += '_' + '_'.join(changed_params)
    return model_name

# Internal Cell
def _as_tuple(x):
    if isinstance(x, tuple):
        return x
    return (x,)

# Cell
class StatsForecast:

    def __init__(self, df, models, freq, n_jobs=1):
        self.ga, self.uids, self.last_dates = _grouped_array_from_df(df)
        self.models = models
        self.freq = pd.tseries.frequencies.to_offset(freq)
        self.n_jobs = n_jobs

    def forecast(self, h):
        if self.n_jobs == 1:
            fcsts = self._sequential_forecast(h)
        else:
            fcsts = self._data_parallel_forecast(h)
        dates = np.hstack([
            pd.date_range(last_date + self.freq, periods=h, freq=self.freq)
            for last_date in self.last_dates
        ])
        idx = pd.Index(np.repeat(self.uids, h), name='unique_id')
        return pd.DataFrame({'ds': dates, **fcsts}, index=idx)

    def _sequential_forecast(self, h):
        fcsts = {}
        logger.info('Computing forecasts')
        for model_args in self.models:
            model, *args = _as_tuple(model_args)
            model_name = _build_forecast_name(model, *args)
            fcsts[model_name] = self.ga.compute_forecasts(h, model, *args)
            logger.info(f'Computed forecasts for {model_name}.')
        return fcsts

    def _data_parallel_forecast(self, h):
        fcsts = {}
        logger.info('Computing forecasts')
        gas = self.ga.split(self.n_jobs)
        with ProcessPoolExecutor(self.n_jobs) as executor:
            for model_args in self.models:
                model, *args = _as_tuple(model_args)
                model_name = _build_forecast_name(model, *args)
                futures = []
                for ga in gas:
                    future = executor.submit(ga.compute_forecasts, h, model, *args)
                    futures.append(future)
                fcsts[model_name] = np.hstack([f.result() for f in futures])
                logger.info(f'Computed forecasts for {model_name}.')
        return fcsts