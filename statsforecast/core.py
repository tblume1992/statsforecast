# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/core.ipynb.

# %% auto 0
__all__ = ['StatsForecast']

# %% ../nbs/core.ipynb 4
import inspect
import logging
from os import cpu_count
from typing import Any, List, Optional

import numpy as np
import pandas as pd
from tqdm.autonotebook import tqdm

# %% ../nbs/core.ipynb 5
logging.basicConfig(
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

# %% ../nbs/core.ipynb 8
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
    
    def fit(self, models):
        fm = np.full((self.n_groups, len(models)), np.nan, dtype=object)
        for i, grp in enumerate(self):
            y = grp[:, 0] if grp.ndim == 2 else grp
            X = grp[:, 1:] if (grp.ndim == 2 and grp.shape[1] > 1) else None
            for i_model, model in enumerate(models):
                new_model = model.new()
                fm[i, i_model] = new_model.fit(y=y, X=X)
        return fm
    
    def _get_cols(self, models, attr, h, X, level=tuple()):
        n_models = len(models)
        cuts = np.full(n_models + 1, fill_value=np.nan, dtype=np.int32)
        has_level_models = np.full(n_models, fill_value=False, dtype=bool) 
        cuts[0] = 0
        for i_model, model in enumerate(models):
            len_cols = 1 # mean
            has_level = 'level' in inspect.signature(getattr(model, attr)).parameters and len(level) > 0
            has_level_models[i_model] = has_level
            if has_level:
                len_cols += 2 * len(level) #levels
            cuts[i_model + 1] = len_cols + cuts[i_model]
        return cuts, has_level_models
    
    def _output_fcst(self, models, attr, h, X, level=tuple()):
        #returns empty output according to method
        cuts, has_level_models = self._get_cols(models=models, attr=attr, h=h, X=X, level=level)
        out = np.full((self.n_groups * h, cuts[-1]), fill_value=np.nan, dtype=np.float32)
        return out, cuts, has_level_models
        
    def predict(self, fm, h, X=None, level=tuple()):
        #fm stands for fitted_models
        #and fm should have fitted_model
        fcsts, cuts, has_level_models = self._output_fcst(
            models=fm[0], attr='predict', 
            h=h, X=X, level=level
        )
        matches = ['mean', 'lo', 'hi']
        cols = []
        for i_model in range(fm.shape[1]):
            has_level = has_level_models[i_model]
            kwargs = {}
            if has_level:
                kwargs['level'] = level
            for i, _ in enumerate(self):
                if X is not None:
                    X_ = X[i]
                else:
                    X_ = None
                res_i = fm[i, i_model].predict(h=h, X=X_, **kwargs)
                cols_m = [key for key in res_i.keys() if any(key.startswith(m) for m in matches)]
                fcsts_i = np.vstack([res_i[key] for key in cols_m]).T
                model_name = repr(fm[i, i_model])
                cols_m = [f'{model_name}' if col == 'mean' else f'{model_name}-{col}' for col in cols_m]
                if fcsts_i.ndim == 1:
                    fcsts_i = fcsts_i[:, None]
                fcsts[i * h : (i + 1) * h, cuts[i_model]:cuts[i_model + 1]] = fcsts_i
            cols += cols_m
        return fcsts, cols
    
    def fit_predict(self, models, h, X=None, level=tuple()):
        #fitted models
        fm = self.fit(models=models)
        #forecasts
        fcsts, cols = self.predict(fm=fm, h=h, X=X, level=level)
        return fm, fcsts, cols
    
    def forecast(self, models, h, fallback_model=None, fitted=False, X=None, level=tuple(), verbose=False):
        fcsts, cuts, has_level_models = self._output_fcst(
            models=models, attr='forecast', 
            h=h, X=X, level=level
        )
        matches = ['mean', 'lo', 'hi']
        matches_fitted = ['fitted', 'fitted-lo', 'fitted-hi']
        if fitted:
            #for the moment we dont return levels for fitted values in 
            #forecast mode
            fitted_vals = np.full((self.data.shape[0], 1 + cuts[-1]), np.nan, dtype=np.float32)
            if self.data.ndim == 1:
                fitted_vals[:, 0] = self.data
            else:
                fitted_vals[:, 0] = self.data[:, 0]
        iterable = tqdm(enumerate(self), 
                        disable=(not verbose), 
                        total=len(self),
                        desc='Forecast')
        for i, grp in iterable:
            y_train = grp[:, 0] if grp.ndim == 2 else grp
            X_train = grp[:, 1:] if (grp.ndim == 2 and grp.shape[1] > 1) else None
            if X is not None:
                X_f = X[i]
            else:
                X_f = None
            cols = []
            cols_fitted = []
            for i_model, model in enumerate(models):
                has_level = has_level_models[i_model]
                kwargs = {}
                if has_level:
                    kwargs['level'] = level
                try:
                    res_i = model.forecast(h=h, y=y_train, X=X_train, X_future=X_f, fitted=fitted, **kwargs)
                except Exception as error:
                    if fallback_model is not None:
                        res_i = fallback_model.forecast(h=h, y=y_train, X=X_train, X_future=X_f, fitted=fitted, **kwargs)
                    else:
                        raise error
                cols_m = [key for key in res_i.keys() if any(key.startswith(m) for m in matches)]
                fcsts_i = np.vstack([res_i[key] for key in cols_m]).T
                cols_m = [f'{repr(model)}' if col == 'mean' else f'{repr(model)}-{col}' for col in cols_m]
                if fcsts_i.ndim == 1:
                    fcsts_i = fcsts_i[:, None]
                fcsts[i * h : (i + 1) * h, cuts[i_model]:cuts[i_model + 1]] = fcsts_i
                cols += cols_m
                if fitted:
                    cols_m_fitted = [key for key in res_i.keys() if any(key.startswith(m) for m in matches_fitted)]
                    fitted_i = np.vstack([res_i[key] for key in cols_m_fitted]).T
                    cols_m_fitted = [f'{repr(model)}' \
                                     if col == 'fitted' else f"{repr(model)}-{col.replace('fitted-', '')}" \
                                     for col in cols_m_fitted]
                    fitted_vals[self.indptr[i] : self.indptr[i + 1], (cuts[i_model] + 1):(cuts[i_model + 1] + 1)] = fitted_i
                    cols_fitted += cols_m_fitted
        result = {'forecasts': fcsts, 'cols': cols}
        if fitted:
            result['fitted'] = {'values': fitted_vals}
            result['fitted']['cols'] = ['y'] + cols_fitted
        return result
    
    def cross_validation(self, models, h, test_size, step_size=1, input_size=None, fitted=False, level=tuple(), 
                         verbose=False):
        # output of size: (ts, window, h)
        if (test_size - h) % step_size:
            raise Exception('`test_size - h` should be module `step_size`')
        n_windows = int((test_size - h) / step_size) + 1
        n_models = len(models)
        cuts, has_level_models = self._get_cols(models=models, attr='forecast', h=h, X=None, level=level)
        # first column of out is the actual y
        out = np.full((self.n_groups, n_windows, h, 1 + cuts[-1]), np.nan, dtype=np.float32)
        if fitted:
            fitted_vals = np.full((self.data.shape[0], n_windows, n_models + 1), np.nan, dtype=np.float32)
            fitted_idxs = np.full((self.data.shape[0], n_windows), False, dtype=bool)
            last_fitted_idxs = np.full_like(fitted_idxs, False, dtype=bool)
        matches = ['mean', 'lo', 'hi']
        steps = list(range(-test_size, -h + 1, step_size))
        for i_ts, grp in enumerate(self):
            iterable = tqdm(enumerate(steps, start=0), 
                            desc=f'Cross Validation Time Series {i_ts + 1}', 
                            disable=(not verbose),
                            total=len(steps))
            for i_window, cutoff in iterable:
                end_cutoff = cutoff + h
                in_size_disp = cutoff if input_size is None else input_size 
                y = grp[(cutoff - in_size_disp):cutoff]
                y_train = y[:, 0] if y.ndim == 2 else y
                X_train = y[:, 1:] if (y.ndim == 2 and y.shape[1] > 1) else None
                y_test = grp[cutoff:] if end_cutoff == 0 else grp[cutoff:end_cutoff]
                X_future = y_test[:, 1:] if (y_test.ndim == 2 and y_test.shape[1] > 1) else None
                out[i_ts, i_window, :, 0] = y_test[:, 0] if y.ndim == 2 else y_test
                if fitted:
                    fitted_vals[self.indptr[i_ts] : self.indptr[i_ts + 1], i_window, 0][
                        (cutoff - in_size_disp):cutoff
                    ] = y_train
                    fitted_idxs[self.indptr[i_ts] : self.indptr[i_ts + 1], i_window][
                        (cutoff - in_size_disp):cutoff
                    ] = True
                    last_fitted_idxs[
                        self.indptr[i_ts] : self.indptr[i_ts + 1], i_window
                    ][cutoff-1] = True
                cols = ['y']
                for i_model, model in enumerate(models):
                    has_level = has_level_models[i_model]
                    kwargs = {}
                    if has_level:
                        kwargs['level'] = level
                    res_i = model.forecast(h=h, y=y_train, X=X_train, X_future=X_future, fitted=fitted, **kwargs)
                    cols_m = [key for key in res_i.keys() if any(key.startswith(m) for m in matches)]
                    fcsts_i = np.vstack([res_i[key] for key in cols_m]).T
                    cols_m = [f'{repr(model)}' if col == 'mean' else f'{repr(model)}-{col}' for col in cols_m]
                    out[i_ts, i_window, :, (1 + cuts[i_model]):(1 + cuts[i_model + 1])] = fcsts_i
                    if fitted:
                        fitted_vals[self.indptr[i_ts] : self.indptr[i_ts + 1], i_window, i_model + 1][
                            (cutoff - in_size_disp):cutoff
                        ] = res_i['fitted']
                    cols += cols_m
        result = {'forecasts': out.reshape(-1, 1 + cuts[-1]), 'cols': cols}
        if fitted:
            result['fitted'] = {
                'values': fitted_vals, 
                'idxs': fitted_idxs, 
                'last_idxs': last_fitted_idxs,
                'cols': ['y'] + [repr(model) for model in models]
            }
        return result

    def split(self, n_chunks):
        return [self[x[0] : x[-1] + 1] for x in np.array_split(range(self.n_groups), n_chunks) if x.size]
    
    def split_fm(self, fm, n_chunks):
        return [fm[x[0] : x[-1] + 1] for x in np.array_split(range(self.n_groups), n_chunks) if x.size]

# %% ../nbs/core.ipynb 17
def _grouped_array_from_df(df, sort_df):
    df = df.set_index('ds', append=True)
    if not df.index.is_monotonic_increasing and sort_df:
        df = df.sort_index()
    data = df.values.astype(np.float32)
    indices_sizes = df.index.get_level_values('unique_id').value_counts(sort=False)
    indices = indices_sizes.index
    sizes = indices_sizes.values
    cum_sizes = sizes.cumsum()
    dates = df.index.get_level_values('ds')[cum_sizes - 1]
    indptr = np.append(0, cum_sizes).astype(np.int32)
    return GroupedArray(data, indptr), indices, dates, df.index

# %% ../nbs/core.ipynb 19
def _cv_dates(last_dates, freq, h, test_size, step_size=1):
    #assuming step_size = 1
    if (test_size - h) % step_size:
        raise Exception('`test_size - h` should be module `step_size`')
    n_windows = int((test_size - h) / step_size) + 1
    if len(np.unique(last_dates)) == 1:
        if issubclass(last_dates.dtype.type, np.integer):
            total_dates = np.arange(last_dates[0] - test_size + 1, last_dates[0] + 1)
            out = np.empty((h * n_windows, 2), dtype=last_dates.dtype)
            freq = 1
        else:
            total_dates = pd.date_range(end=last_dates[0], periods=test_size, freq=freq)
            out = np.empty((h * n_windows, 2), dtype='datetime64[s]')
        for i_window, cutoff in enumerate(range(-test_size, -h + 1, step_size), start=0):
            end_cutoff = cutoff + h
            out[h * i_window : h * (i_window + 1), 0] = total_dates[cutoff:] if end_cutoff == 0 else total_dates[cutoff:end_cutoff]
            out[h * i_window : h * (i_window + 1), 1] = np.tile(total_dates[cutoff] - freq, h)
        dates = pd.DataFrame(np.tile(out, (len(last_dates), 1)), columns=['ds', 'cutoff'])
    else:
        dates = pd.concat([_cv_dates(np.array([ld]), freq, h, test_size, step_size) for ld in last_dates])
        dates = dates.reset_index(drop=True)
    return dates

# %% ../nbs/core.ipynb 23
def _get_n_jobs(n_groups, n_jobs, ray_address):
    if ray_address is not None:
        logger.info(
            'Using ray address,'
            'using available resources insted of `n_jobs`'
        )
        try:
            import ray
        except ModuleNotFoundError as e:
            msg = (
                f'{e}. To use a ray cluster you have to install '
                'ray. Please run `pip install ray`. '
            )
            raise ModuleNotFoundError(msg) from e
        if not ray.is_initialized():
            ray.init(ray_address, ignore_reinit_error=True)
        actual_n_jobs = int(ray.available_resources()['CPU'])
    else:
        if n_jobs == -1 or (n_jobs is None):
            actual_n_jobs = cpu_count()
        else:
            actual_n_jobs = n_jobs
    return min(n_groups, actual_n_jobs)

# %% ../nbs/core.ipynb 27
class StatsForecast:
    
    def __init__(
            self, 
            models: List[Any],
            freq: str,
            n_jobs: int = 1,
            ray_address: Optional[str] = None,
            df: Optional[pd.DataFrame] = None,
            sort_df: bool = True,
            fallback_model: Any = None,
            verbose: bool = False
        ):
        """core.StatsForecast.
        [Source code](https://github.com/Nixtla/statsforecast/blob/main/statsforecast/core.py).

        The `core.StatsForecast` class allows you to efficiently fit multiple `StatsForecast` models 
        for large sets of time series. It operates with pandas DataFrame `df` that identifies series 
        and datestamps with the `unique_id` and `ds` columns. The `y` column denotes the target 
        time series variable. 

        The class has memory-efficient `StatsForecast.forecast` method that avoids storing partial 
        model outputs. While the `StatsForecast.fit` and `StatsForecast.predict` methods with 
        Scikit-learn interface store the fitted models.

        **Parameters:**<br>
        `df`: pandas.DataFrame, with columns [`unique_id`, `ds`, `y`] and exogenous.<br>
        `models`: List[typing.Any], list of instantiated objects models.StatsForecast.<br>
        `freq`: str, frequency of the data, [panda's available frequencies](https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#offset-aliases).<br>
        `n_jobs`: int, number of jobs used in the parallel processing, use -1 for all cores.<br>
        `sort_df`: bool, if True, sort `df` by [`unique_id`,`ds`].<br>
        `fallback_model`: Any, Model to be used if a model fails. Only works with the `forecast` method.<br>
        `verbose`: bool, Wether print progress bar. Only used when `n_jobs=1`.<br>

        **Notes:**<br>
        The `core.StatsForecast` class offers parallelization utilities with Dask, Spark and Ray back-ends.<br>
        See distributed computing example [here](https://github.com/Nixtla/statsforecast/tree/main/experiments/ray).
        """
        # TODO @fede: needed for residuals, think about it later
        self.models = models
        self.freq = pd.tseries.frequencies.to_offset(freq)
        self.n_jobs = n_jobs
        self.ray_address = ray_address
        self.fallback_model = fallback_model
        self._prepare_fit(df=df, sort_df=sort_df)
        self.verbose = verbose and self.n_jobs == 1
        
    def _prepare_fit(self, df, sort_df):
        if df is not None:
            if df.index.name != 'unique_id':
                df = df.set_index('unique_id')
            self.ga, self.uids, self.last_dates, self.ds = _grouped_array_from_df(df, sort_df)
            self.n_jobs = _get_n_jobs(len(self.ga), self.n_jobs, self.ray_address)
            self.sort_df = sort_df
        
    def fit(
            self,
            df: Optional[pd.DataFrame] = None, 
            sort_df: bool = True 
        ):
        """Fit the core.StatsForecast.

        Fit `models` to a large set of time series from DataFrame `df`.
        and store fitted models for later inspection.

        **Parameters:**<br>
        `df`: pandas.DataFrame, with columns [`unique_id`, `ds`, `y`] and exogenous.<br>
        `sort_df`: bool, if True, sort `df` by [`unique_id`,`ds`].<br>

        **Returns:**<br>
        `self`: Returns with stored `StatsForecast` fitted `models`.
        """
        self._prepare_fit(df, sort_df)
        if self.n_jobs == 1:
            self.fitted_ = self.ga.fit(models=self.models)
        else:
            self.fitted_ = self._fit_parallel()
        return self
    
    def _make_future_df(self, h: int):
        if issubclass(self.last_dates.dtype.type, np.integer):
            last_date_f = lambda x: np.arange(x + 1, x + 1 + h, dtype=self.last_dates.dtype)
        else:
            last_date_f = lambda x: pd.date_range(x + self.freq, periods=h, freq=self.freq)
        if len(np.unique(self.last_dates)) == 1:
            dates = np.tile(last_date_f(self.last_dates[0]), len(self.ga))
        else:
            dates = np.hstack([
                last_date_f(last_date)
                for last_date in self.last_dates            
            ])
        idx = pd.Index(np.repeat(self.uids, h), name='unique_id')
        df = pd.DataFrame({'ds': dates}, index=idx)
        return df
    
    def _parse_X_level(self, h, X, level):
        if X is not None:
            if X.index.name != 'unique_id':
                X = X.set_index('unique_id')
            expected_shape = (h * len(self.ga), self.ga.data.shape[1])
            if X.shape != expected_shape:
                raise ValueError(f'Expected X to have shape {expected_shape}, but got {X.shape}')
            X, _, _, _ = _grouped_array_from_df(X, sort_df=self.sort_df)
        if level is None:
            level = tuple()
        return X, level
    
    def predict(
            self,
            h: int,
            X_df: Optional[pd.DataFrame] = None,
            level: Optional[List[int]] = None,
        ):
        """Predict with core.StatsForecast.

        Use stored fitted `models` to predict large set of time series from DataFrame `df`.        

        **Parameters:**<br>
        `h`: int, forecast horizon.<br>
        `X_df`: pandas.DataFrame, with [`unique_id`, `ds`] columns and `df`'s future exogenous.<br>
        `level`: float list 0-100, confidence levels for prediction intervals.<br>

        **Returns:**<br>
        `fcsts_df`: pandas.DataFrame, with `models` columns for point predictions and probabilistic
        predictions for all fitted `models`.<br>
        """
        X, level = self._parse_X_level(h=h, X=X_df, level=level)
        if self.n_jobs == 1:
            fcsts, cols = self.ga.predict(fm=self.fitted_, h=h, X=X, level=level)
        else:
            fcsts, cols = self._predict_parallel(h=h, X=X, level=level)
        fcsts_df = self._make_future_df(h=h)
        fcsts_df[cols] = fcsts
        return fcsts_df
    
    def fit_predict(
            self,
            h: int,
            df: Optional[pd.DataFrame] = None,
            X_df: Optional[pd.DataFrame] = None,
            level: Optional[List[int]] = None,
            sort_df: bool = True
        ):
        """Fit and Predict with core.StatsForecast.

        This method avoids memory burden due from object storage.
        It is analogous to Scikit-Learn `fit_predict` without storing information.
        It requires the forecast horizon `h` in advance. 
        
        In contrast to `StatsForecast.forecast` this method stores partial models outputs.

        **Parameters:**<br>
        `h`: int, forecast horizon.<br>
        `df`: pandas.DataFrame, with columns [`unique_id`, `ds`, `y`] and exogenous.
        `X_df`: pandas.DataFrame, with [`unique_id`, `ds`] columns and `df`'s future exogenous.<br>
        `level`: float list 0-100, confidence levels for prediction intervals.<br>
        `sort_df`: bool, if True, sort `df` by [`unique_id`,`ds`].

        **Returns:**<br>
        `fcsts_df`: pandas.DataFrame, with `models` columns for point predictions and probabilistic
        predictions for all fitted `models`.<br>
        """
        self._prepare_fit(df, sort_df)
        X, level = self._parse_X_level(h=h, X=X_df, level=level)
        if self.n_jobs == 1:
            self.fitted_, fcsts, cols = self.ga.fit_predict(models=self.models, h=h, X=X, level=level)
        else:
            self.fitted_, fcsts, cols = self._fit_predict_parallel(h=h, X=X, level=level)
        fcsts_df = self._make_future_df(h=h)
        fcsts_df[cols] = fcsts
        return fcsts_df
    
    def forecast(
            self,
            h: int,
            df: Optional[pd.DataFrame] = None,
            X_df: Optional[pd.DataFrame] = None,
            level: Optional[List[int]] = None,
            fitted: bool = False,
            sort_df: bool = True
        ):
        """Memory Efficient core.StatsForecast predictions.

        This method avoids memory burden due from object storage.
        It is analogous to Scikit-Learn `fit_predict` without storing information.
        It requires the forecast horizon `h` in advance.

        **Parameters:**<br>
        `h`: int, forecast horizon.<br>
        `df`: pandas.DataFrame, with columns [`unique_id`, `ds`, `y`] and exogenous.<br>
        `X_df`: pandas.DataFrame, with [`unique_id`, `ds`] columns and `df`'s future exogenous.<br>
        `level`: float list 0-100, confidence levels for prediction intervals.<br>
        `fitted`: bool, wether or not returns insample predictions.<br>
        `sort_df`: bool, if True, sort `df` by [`unique_id`,`ds`].<br>

        **Returns:**<br>
        `fcsts_df`: pandas.DataFrame, with `models` columns for point predictions and probabilistic
        predictions for all fitted `models`.<br>
        """
        self._prepare_fit(df, sort_df)
        X, level = self._parse_X_level(h=h, X=X_df, level=level)
        if self.n_jobs == 1:
            res_fcsts = self.ga.forecast(models=self.models, 
                                         h=h, fallback_model=self.fallback_model, 
                                         fitted=fitted, X=X, level=level, 
                                         verbose=self.verbose)
        else:
            res_fcsts = self._forecast_parallel(h=h, fitted=fitted, X=X, level=level)
        if fitted:
            self.fcst_fitted_values_ = res_fcsts['fitted']
        fcsts = res_fcsts['forecasts']
        cols = res_fcsts['cols']
        fcsts_df = self._make_future_df(h=h)
        fcsts_df[cols] = fcsts
        return fcsts_df
    
    def forecast_fitted_values(self):
        """Access core.StatsForecast insample predictions.

        After executing `StatsForecast.forecast`, you can access the insample 
        prediction values for each model. To get them, you need to pass `fitted=True` 
        to the `StatsForecast.forecast` method and then use the 
        `StatsForecast.forecast_fitted_values` method.

        **Parameters:**<br>
        Check `StatsForecast.forecast` parameters, use `fitted=True`.<br>

        **Returns:**<br>
        `fcsts_df`: pandas.DataFrame, with insample `models` columns for point predictions and probabilistic
        predictions for all fitted `models`.<br>
        """
        if not hasattr(self, "fcst_fitted_values_"):
            raise Exception("Please run `forecast` mehtod using `fitted=True`")
        cols = self.fcst_fitted_values_["cols"]
        df = pd.DataFrame(
            self.fcst_fitted_values_["values"], columns=cols, index=self.ds
        ).reset_index(level=1)
        return df
    
    def cross_validation(
            self,
            h: int,
            df: Optional[pd.DataFrame] = None,
            n_windows: int = 1,
            step_size: int = 1,
            test_size: Optional[int] = None,
            input_size: Optional[int] = None,
            level: Optional[List[int]] = None,
            fitted: bool = False,
            sort_df: bool = True
        ):
        """Temporal Cross-Validation with core.StatsForecast.

        `core.StatsForecast`'s cross-validation efficiently fits a list of StatsForecast 
        models through multiple training windows, in either chained or rolled manner.
        
        `StatsForecast.models`' speed allows to overcome this evaluation technique 
        high computational costs. Temporal cross-validation provides better model's 
        generalization measurements by increasing the test's length and diversity.

        **Parameters:**<br>
        `h`: int, forecast horizon.<br>
        `df`: pandas.DataFrame, with columns [`unique_id`, `ds`, `y`] and exogenous.<br>
        `n_windows`: int, number of windows used for cross validation.<br>
        `step_size`: int = 1, step size between each window.<br>
        `test_size`: Optional[int] = None, length of test size. If passed, set `n_windows=None`.<br>
        `input_size`: Optional[int] = None, input size for each window, if not none rolled windows.<br>
        `level`: float list 0-100, confidence levels for prediction intervals.<br>
        `fitted`: bool, wether or not returns insample predictions.<br>
        `sort_df`: bool, if True, sort `df` by `unique_id` and `ds`.

        **Returns:**<br>
        `fcsts_df`: pandas.DataFrame, with insample `models` columns for point predictions and probabilistic
        predictions for all fitted `models`.<br>
        
        
        """
        if test_size is None:
            test_size = h + step_size * (n_windows - 1)
        elif n_windows is None:
            if (test_size - h) % step_size:
                raise Exception('`test_size - h` should be module `step_size`')
            n_windows = int((test_size - h) / step_size) + 1
        elif (n_windows is None) and (test_size is None):
            raise Exception('you must define `n_windows` or `test_size`')
        else:
            raise Exception('you must define `n_windows` or `test_size` but not both')
        self._prepare_fit(df, sort_df)
        _, level = self._parse_X_level(h=h, X=None, level=level)
        if self.n_jobs == 1:
            res_fcsts = self.ga.cross_validation(
                models=self.models, h=h, test_size=test_size, 
                step_size=step_size, 
                input_size=input_size, 
                fitted=fitted,
                level=level,
                verbose=self.verbose
            )
        else:
            res_fcsts = self._cross_validation_parallel(
                h=h, 
                test_size=test_size,
                step_size=step_size,
                input_size=input_size,
                fitted=fitted,
                level=level
            )
            
        if fitted:
            self.cv_fitted_values_ = res_fcsts['fitted']
            self.n_cv_ = n_windows
            
        fcsts = res_fcsts['forecasts']
        cols = res_fcsts['cols']
        fcsts_df = _cv_dates(last_dates=self.last_dates, freq=self.freq, 
                             h=h, test_size=test_size, step_size=step_size)
        idx = pd.Index(np.repeat(self.uids, h * n_windows), name='unique_id')
        fcsts_df.index = idx
        fcsts_df[cols] = fcsts
        return fcsts_df
    
    def cross_validation_fitted_values(self):
        """Access core.StatsForecast insample cross validated predictions.

        After executing `StatsForecast.cross_validation`, you can access the insample 
        prediction values for each model and window. To get them, you need to pass `fitted=True` 
        to the `StatsForecast.cross_validation` method and then use the 
        `StatsForecast.cross_validation_fitted_values` method.

        **Parameters:**<br>
        Check `StatsForecast.cross_validation` parameters, use `fitted=True`.<br>

        **Returns:**<br>
        `fcsts_df`: pandas.DataFrame, with insample `models` columns for point predictions 
        and probabilistic predictions for all fitted `models`.<br>
        """
        if not hasattr(self, 'cv_fitted_values_'):
            raise Exception('Please run `cross_validation` mehtod using `fitted=True`')
        index = pd.MultiIndex.from_tuples(np.tile(self.ds, self.n_cv_), names=['unique_id', 'ds'])
        df = pd.DataFrame(index=index)
        df['cutoff'] = self.cv_fitted_values_['last_idxs'].flatten(order='F')
        df[self.cv_fitted_values_['cols']] = np.reshape(self.cv_fitted_values_['values'], (-1, len(self.models) + 1), order='F')
        idxs = self.cv_fitted_values_['idxs'].flatten(order='F')
        df = df.iloc[idxs].reset_index(level=1)
        df['cutoff'] = df['ds'].where(df['cutoff']).bfill()
        return df

    def _get_pool(self):
        if self.ray_address is not None:
            try:
                from ray.util.multiprocessing import Pool
            except ModuleNotFoundError as e:
                msg = (
                    f'{e}. To use a ray cluster you have to install '
                    'ray. Please run `pip install ray`. '
                )
                raise ModuleNotFoundError(msg) from e
            pool_kwargs = dict(ray_address=self.ray_address)
        else:
            from multiprocessing import Pool
            pool_kwargs = dict()
        return Pool, pool_kwargs
    
    def _fit_parallel(self):
        gas = self.ga.split(self.n_jobs)
        Pool, pool_kwargs = self._get_pool()
        with Pool(self.n_jobs, **pool_kwargs) as executor:
            futures = []
            for ga in gas:
                future = executor.apply_async(ga.fit, (self.models,))
                futures.append(future)
            fm = np.vstack([f.get() for f in futures])
        return fm
    
    def _get_gas_Xs(self, X):
        gas = self.ga.split(self.n_jobs)
        if X is not None:
            Xs = X.split(self.n_jobs)
        else:
            from itertools import repeat
            Xs = repeat(None)
        return gas, Xs
    
    def _predict_parallel(self, h, X, level):
        #create elements for each core
        gas, Xs = self._get_gas_Xs(X=X)
        fms = self.ga.split_fm(self.fitted_, self.n_jobs)
        Pool, pool_kwargs = self._get_pool()
        #compute parallel forecasts
        with Pool(self.n_jobs, **pool_kwargs) as executor:
            futures = []
            for ga, fm, X_ in zip(gas, fms, Xs):
                future = executor.apply_async(ga.predict, (fm, h, X_, level,))
                futures.append(future)
            out = [f.get() for f in futures]
            fcsts, cols = list(zip(*out))
            fcsts = np.vstack(fcsts)
            cols = cols[0]
        return fcsts, cols
    
    def _fit_predict_parallel(self, h, X, level):
        #create elements for each core
        gas, Xs = self._get_gas_Xs(X=X)
        Pool, pool_kwargs = self._get_pool()
        #compute parallel forecasts
        with Pool(self.n_jobs, **pool_kwargs) as executor:
            futures = []
            for ga, X_ in zip(gas, Xs):
                future = executor.apply_async(ga.fit_predict, (self.models, h, X_, level,))
                futures.append(future)
            out = [f.get() for f in futures]
            fm, fcsts, cols = list(zip(*out))
            fm = np.vstack(fm)
            fcsts = np.vstack(fcsts)
            cols = cols[0]
        return fm, fcsts, cols
    
    def _forecast_parallel(self, h, fitted, X, level):
        #create elements for each core
        gas, Xs = self._get_gas_Xs(X=X)
        Pool, pool_kwargs = self._get_pool()
        #compute parallel forecasts
        result = {}
        with Pool(self.n_jobs, **pool_kwargs) as executor:
            futures = []
            for ga, X_ in zip(gas, Xs):
                future = executor.apply_async(
                    ga.forecast, 
                    (self.models, h, self.fallback_model, fitted, X_, level,)
                )
                futures.append(future)
            out = [f.get() for f in futures]
            fcsts = [d['forecasts'] for d in out]
            fcsts = np.vstack(fcsts)
            cols = out[0]['cols']
            result['forecasts'] = fcsts
            result['cols'] = cols
            if fitted:
                result['fitted'] = {}
                fitted_vals = [d['fitted']['values'] for d in out]
                result['fitted']['values'] = np.vstack(fitted_vals)
                result['fitted']['cols'] = out[0]['fitted']['cols']
        return result
    
    def _cross_validation_parallel(self, h, test_size, step_size, input_size, fitted, level):
        #create elements for each core
        gas = self.ga.split(self.n_jobs)
        Pool, pool_kwargs = self._get_pool()
        #compute parallel forecasts
        result = {}
        with Pool(self.n_jobs, **pool_kwargs) as executor:
            futures = []
            for ga in gas:
                future = executor.apply_async(
                    ga.cross_validation, 
                    (self.models, h, test_size, step_size, input_size, fitted, level,)
                )
                futures.append(future)
            out = [f.get() for f in futures]
            fcsts = [d['forecasts'] for d in out]
            fcsts = np.vstack(fcsts)
            cols = out[0]['cols']
            result['forecasts'] = fcsts
            result['cols'] = cols
            if fitted:
                result['fitted'] = {}
                result['fitted']['values'] = np.concatenate([d['fitted']['values'] for d in out])
                for key in ['last_idxs', 'idxs']:
                    result['fitted'][key] = np.concatenate([d['fitted'][key] for d in out])
                result['fitted']['cols'] = out[0]['fitted']['cols']
        return result
    
    def __repr__(self):
        return f"StatsForecast(models=[{','.join(map(repr, self.models))}])"
