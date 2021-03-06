# Copyright (c) 2016,2017 MetPy Developers.
# Distributed under the terms of the BSD 3-Clause License.
# SPDX-License-Identifier: BSD-3-Clause
"""Contains a collection of generally useful calculation tools."""

import functools
import warnings

import numpy as np
import numpy.ma as ma
from scipy.spatial import cKDTree

from . import height_to_pressure_std, pressure_to_height_std
from ..package_tools import Exporter
from ..units import check_units, units

exporter = Exporter(globals())


@exporter.export
def resample_nn_1d(a, centers):
    """Return one-dimensional nearest-neighbor indexes based on user-specified centers.

    Parameters
    ----------
    a : array-like
        1-dimensional array of numeric values from which to
        extract indexes of nearest-neighbors
    centers : array-like
        1-dimensional array of numeric values representing a subset of values to approximate

    Returns
    -------
        An array of indexes representing values closest to given array values

    """
    ix = []
    for center in centers:
        index = (np.abs(a - center)).argmin()
        if index not in ix:
            ix.append(index)
    return ix


@exporter.export
def nearest_intersection_idx(a, b):
    """Determine the index of the point just before two lines with common x values.

    Parameters
    ----------
    a : array-like
        1-dimensional array of y-values for line 1
    b : array-like
        1-dimensional array of y-values for line 2

    Returns
    -------
        An array of indexes representing the index of the values
        just before the intersection(s) of the two lines.

    """
    # Difference in the two y-value sets
    difference = a - b

    # Determine the point just before the intersection of the lines
    # Will return multiple points for multiple intersections
    sign_change_idx, = np.nonzero(np.diff(np.sign(difference)))

    return sign_change_idx


@exporter.export
@units.wraps(('=A', '=B'), ('=A', '=B', '=B'))
def find_intersections(x, a, b, direction='all'):
    """Calculate the best estimate of intersection.

    Calculates the best estimates of the intersection of two y-value
    data sets that share a common x-value set.

    Parameters
    ----------
    x : array-like
        1-dimensional array of numeric x-values
    a : array-like
        1-dimensional array of y-values for line 1
    b : array-like
        1-dimensional array of y-values for line 2
    direction : string, optional
        specifies direction of crossing. 'all', 'increasing' (a becoming greater than b),
        or 'decreasing' (b becoming greater than a). Defaults to 'all'.

    Returns
    -------
        A tuple (x, y) of array-like with the x and y coordinates of the
        intersections of the lines.

    """
    # Find the index of the points just before the intersection(s)
    nearest_idx = nearest_intersection_idx(a, b)
    next_idx = nearest_idx + 1

    # Determine the sign of the change
    sign_change = np.sign(a[next_idx] - b[next_idx])

    # x-values around each intersection
    _, x0 = _next_non_masked_element(x, nearest_idx)
    _, x1 = _next_non_masked_element(x, next_idx)

    # y-values around each intersection for the first line
    _, a0 = _next_non_masked_element(a, nearest_idx)
    _, a1 = _next_non_masked_element(a, next_idx)

    # y-values around each intersection for the second line
    _, b0 = _next_non_masked_element(b, nearest_idx)
    _, b1 = _next_non_masked_element(b, next_idx)

    # Calculate the x-intersection. This comes from finding the equations of the two lines,
    # one through (x0, a0) and (x1, a1) and the other through (x0, b0) and (x1, b1),
    # finding their intersection, and reducing with a bunch of algebra.
    delta_y0 = a0 - b0
    delta_y1 = a1 - b1
    intersect_x = (delta_y1 * x0 - delta_y0 * x1) / (delta_y1 - delta_y0)

    # Calculate the y-intersection of the lines. Just plug the x above into the equation
    # for the line through the a points. One could solve for y like x above, but this
    # causes weirder unit behavior and seems a little less good numerically.
    intersect_y = ((intersect_x - x0) / (x1 - x0)) * (a1 - a0) + a0

    # If there's no intersections, return
    if len(intersect_x) == 0:
        return intersect_x, intersect_y

    # Check for duplicates
    duplicate_mask = (np.ediff1d(intersect_x, to_end=1) != 0)

    # Make a mask based on the direction of sign change desired
    if direction == 'increasing':
        mask = sign_change > 0
    elif direction == 'decreasing':
        mask = sign_change < 0
    elif direction == 'all':
        return intersect_x[duplicate_mask], intersect_y[duplicate_mask]
    else:
        raise ValueError('Unknown option for direction: {0}'.format(str(direction)))

    return intersect_x[mask & duplicate_mask], intersect_y[mask & duplicate_mask]


@exporter.export
def interpolate_nans(x, y, kind='linear'):
    """Interpolate NaN values in y.

    Interpolate NaN values in the y dimension. Works with unsorted x values.

    Parameters
    ----------
    x : array-like
        1-dimensional array of numeric x-values
    y : array-like
        1-dimensional array of numeric y-values
    kind : string
        specifies the kind of interpolation x coordinate - 'linear' or 'log', optional.
        Defaults to 'linear'.

    Returns
    -------
        An array of the y coordinate data with NaN values interpolated.

    """
    x_sort_args = np.argsort(x)
    x = x[x_sort_args]
    y = y[x_sort_args]
    nans = np.isnan(y)
    if kind == 'linear':
        y[nans] = np.interp(x[nans], x[~nans], y[~nans])
    elif kind == 'log':
        y[nans] = np.interp(np.log(x[nans]), np.log(x[~nans]), y[~nans])
    else:
        raise ValueError('Unknown option for kind: {0}'.format(str(kind)))
    return y[x_sort_args]


def _next_non_masked_element(a, idx):
    """Return the next non masked element of a masked array.

    If an array is masked, return the next non-masked element (if the given index is masked).
    If no other unmasked points are after the given masked point, returns none.

    Parameters
    ----------
    a : array-like
        1-dimensional array of numeric values
    idx : integer
        index of requested element

    Returns
    -------
        Index of next non-masked element and next non-masked element

    """
    try:
        next_idx = idx + a[idx:].mask.argmin()
        if ma.is_masked(a[next_idx]):
            return None, None
        else:
            return next_idx, a[next_idx]
    except (AttributeError, TypeError, IndexError):
        return idx, a[idx]


def delete_masked_points(*arrs):
    """Delete masked points from arrays.

    Takes arrays and removes masked points to help with calculations and plotting.

    Parameters
    ----------
    arrs : one or more array-like
        source arrays

    Returns
    -------
    arrs : one or more array-like
        arrays with masked elements removed

    """
    if any(hasattr(a, 'mask') for a in arrs):
        keep = ~functools.reduce(np.logical_or, (np.ma.getmaskarray(a) for a in arrs))
        return tuple(ma.asarray(a[keep]) for a in arrs)
    else:
        return arrs


@exporter.export
def reduce_point_density(points, radius, priority=None):
    r"""Return a mask to reduce the density of points in irregularly-spaced data.

    This function is used to down-sample a collection of scattered points (e.g. surface
    data), returning a mask that can be used to select the points from one or more arrays
    (e.g. arrays of temperature and dew point). The points selected can be controlled by
    providing an array of ``priority`` values (e.g. rainfall totals to ensure that
    stations with higher precipitation remain in the mask).

    Parameters
    ----------
    points : (N, K) array-like
        N locations of the points in K dimensional space
    radius : float
        minimum radius allowed between points
    priority : (N, K) array-like, optional
        If given, this should have the same shape as ``points``; these values will
        be used to control selection priority for points.

    Returns
    -------
        (N,) array-like of boolean values indicating whether points should be kept. This
        can be used directly to index numpy arrays to return only the desired points.

    Examples
    --------
    >>> metpy.calc.reduce_point_density(np.array([1, 2, 3]), 1.)
    array([ True, False,  True], dtype=bool)
    >>> metpy.calc.reduce_point_density(np.array([1, 2, 3]), 1.,
    ... priority=np.array([0.1, 0.9, 0.3]))
    array([False,  True, False], dtype=bool)

    """
    # Handle 1D input
    if points.ndim < 2:
        points = points.reshape(-1, 1)

    # Make a kd-tree to speed searching of data.
    tree = cKDTree(points)

    # Need to use sorted indices rather than sorting the position
    # so that the keep mask matches *original* order.
    if priority is not None:
        # Need to sort the locations in decreasing priority.
        sorted_indices = np.argsort(priority)[::-1]
    else:
        # Take advantage of iterator nature of range here to avoid making big lists
        sorted_indices = range(len(points))

    # Keep all points initially
    keep = np.ones(len(points), dtype=np.bool)

    # Loop over all the potential points
    for ind in sorted_indices:
        # Only proceed if we haven't already excluded this point
        if keep[ind]:
            # Find the neighbors and eliminate them
            neighbors = tree.query_ball_point(points[ind], radius)
            keep[neighbors] = False

            # We just removed ourselves, so undo that
            keep[ind] = True

    return keep


def _get_bound_pressure_height(pressure, bound, heights=None, interpolate=True):
    """Calculate the bounding pressure and height in a layer.

    Given pressure, optional heights, and a bound, return either the closest pressure/height
    or interpolated pressure/height. If no heights are provided, a standard atmosphere is
    assumed.

    Parameters
    ----------
    pressure : `pint.Quantity`
        Atmospheric pressures
    bound : `pint.Quantity`
        Bound to retrieve (in pressure or height)
    heights : `pint.Quantity`, optional
        Atmospheric heights associated with the pressure levels. Defaults to using
        heights calculated from ``pressure`` assuming a standard atmosphere.
    interpolate : boolean, optional
        Interpolate the bound or return the nearest. Defaults to True.

    Returns
    -------
    `pint.Quantity`
        The bound pressure and height.

    """
    # Make sure pressure is monotonically decreasing
    sort_inds = np.argsort(pressure)[::-1]
    pressure = pressure[sort_inds]
    if heights is not None:
        heights = heights[sort_inds]

    # Bound is given in pressure
    if bound.dimensionality == {'[length]': -1.0, '[mass]': 1.0, '[time]': -2.0}:
        # If the bound is in the pressure data, we know the pressure bound exactly
        if bound in pressure:
            bound_pressure = bound
            # If we have heights, we know the exact height value, otherwise return standard
            # atmosphere height for the pressure
            if heights is not None:
                bound_height = heights[pressure == bound_pressure]
            else:
                bound_height = pressure_to_height_std(bound_pressure)
        # If bound is not in the data, return the nearest or interpolated values
        else:
            if interpolate:
                bound_pressure = bound  # Use the user specified bound
                if heights is not None:  # Interpolate heights from the height data
                    bound_height = log_interp(bound_pressure, pressure, heights)
                else:  # If not heights given, use the standard atmosphere
                    bound_height = pressure_to_height_std(bound_pressure)
            else:  # No interpolation, find the closest values
                idx = (np.abs(pressure - bound)).argmin()
                bound_pressure = pressure[idx]
                if heights is not None:
                    bound_height = heights[idx]
                else:
                    bound_height = pressure_to_height_std(bound_pressure)

    # Bound is given in height
    elif bound.dimensionality == {'[length]': 1.0}:
        # If there is height data, see if we have the bound or need to interpolate/find nearest
        if heights is not None:
            if bound in heights:  # Bound is in the height data
                bound_height = bound
                bound_pressure = pressure[heights == bound]
            else:  # Bound is not in the data
                if interpolate:
                    bound_height = bound

                    # Need to cast back to the input type since interp (up to at least numpy
                    # 1.13 always returns float64. This can cause upstream users problems,
                    # resulting in something like np.append() to upcast.
                    bound_pressure = np.interp(np.atleast_1d(bound), heights,
                                               pressure).astype(bound.dtype) * pressure.units
                else:
                    idx = (np.abs(heights - bound)).argmin()
                    bound_pressure = pressure[idx]
                    bound_height = heights[idx]
        else:  # Don't have heights, so assume a standard atmosphere
            bound_height = bound
            bound_pressure = height_to_pressure_std(bound)
            # If interpolation is on, this is all we need, if not, we need to go back and
            # find the pressure closest to this and refigure the bounds
            if not interpolate:
                idx = (np.abs(pressure - bound_pressure)).argmin()
                bound_pressure = pressure[idx]
                bound_height = pressure_to_height_std(bound_pressure)

    # Bound has invalid units
    else:
        raise ValueError('Bound must be specified in units of length or pressure.')

    # If the bound is out of the range of the data, we shouldn't extrapolate
    if not (_greater_or_close(bound_pressure, np.nanmin(pressure) * pressure.units) and
            _less_or_close(bound_pressure, np.nanmax(pressure) * pressure.units)):
        raise ValueError('Specified bound is outside pressure range.')
    if heights is not None:
        if not (_less_or_close(bound_height, np.nanmax(heights) * heights.units) and
                _greater_or_close(bound_height, np.nanmin(heights) * heights.units)):
            raise ValueError('Specified bound is outside height range.')

    return bound_pressure, bound_height


@exporter.export
@check_units('[length]')
def get_layer_heights(heights, depth, *args, **kwargs):
    """Return an atmospheric layer from upper air data with the requested bottom and depth.

    This function will subset an upper air dataset to contain only the specified layer using
    the heights only.

    Parameters
    ----------
    heights : array-like
        Atmospheric heights
    depth : `pint.Quantity`
        The thickness of the layer
    *args : array-like
        Atmospheric variable(s) measured at the given pressures
    bottom : `pint.Quantity`, optional
        The bottom of the layer
    interpolate : bool, optional
        Interpolate the top and bottom points if they are not in the given data. Defaults
        to True.
    with_agl : bool, optional
        Returns the heights as above ground level by subtracting the minimum height in the
        provided heights. Defaults to False.

    Returns
    -------
    `pint.Quantity, pint.Quantity`
        The height and data variables of the layer

    """
    bottom = kwargs.pop('bottom', None)
    interpolate = kwargs.pop('interpolate', True)
    with_agl = kwargs.pop('with_agl', False)

    # Make sure pressure and datavars are the same length
    for datavar in args:
        if len(heights) != len(datavar):
            raise ValueError('Height and data variables must have the same length.')

    # If we want things in AGL, subtract the minimum height from all height values
    if with_agl:
        sfc_height = np.min(heights)
        heights -= sfc_height

    # If the bottom is not specified, make it the surface
    if bottom is None:
        bottom = heights[0]

    # Make heights and arguments base units
    heights = heights.to_base_units()
    bottom = bottom.to_base_units()

    # Calculate the top of the layer
    top = bottom + depth

    ret = []  # returned data variables in layer

    # Ensure heights are sorted in ascending order
    sort_inds = np.argsort(heights)
    heights = heights[sort_inds]

    # Mask based on top and bottom
    inds = _greater_or_close(heights, bottom) & _less_or_close(heights, top)
    heights_interp = heights[inds]

    # Interpolate heights at bounds if necessary and sort
    if interpolate:
        # If we don't have the bottom or top requested, append them
        if top not in heights_interp:
            heights_interp = np.sort(np.append(heights_interp, top)) * heights.units
        if bottom not in heights_interp:
            heights_interp = np.sort(np.append(heights_interp, bottom)) * heights.units

    ret.append(heights_interp)

    for datavar in args:
        # Ensure that things are sorted in ascending order
        datavar = datavar[sort_inds]

        if interpolate:
            # Interpolate for the possibly missing bottom/top values
            datavar_interp = interp(heights_interp, heights, datavar)
            datavar = datavar_interp
        else:
            datavar = datavar[inds]

        ret.append(datavar)
    return ret


@exporter.export
@check_units('[pressure]')
def get_layer(pressure, *args, **kwargs):
    r"""Return an atmospheric layer from upper air data with the requested bottom and depth.

    This function will subset an upper air dataset to contain only the specified layer. The
    bottom of the layer can be specified with a pressure or height above the surface
    pressure. The bottom defaults to the surface pressure. The depth of the layer can be
    specified in terms of pressure or height above the bottom of the layer. If the top and
    bottom of the layer are not in the data, they are interpolated by default.

    Parameters
    ----------
    pressure : array-like
        Atmospheric pressure profile
    *args : array-like
        Atmospheric variable(s) measured at the given pressures
    heights: array-like, optional
        Atmospheric heights corresponding to the given pressures. Defaults to using
        heights calculated from ``p`` assuming a standard atmosphere.
    bottom : `pint.Quantity`, optional
        The bottom of the layer as a pressure or height above the surface pressure. Defaults
        to the lowest pressure or height given.
    depth : `pint.Quantity`, optional
        The thickness of the layer as a pressure or height above the bottom of the layer.
        Defaults to 100 hPa.
    interpolate : bool, optional
        Interpolate the top and bottom points if they are not in the given data. Defaults
        to True.

    Returns
    -------
    `pint.Quantity, pint.Quantity`
        The pressure and data variables of the layer

    """
    # Pop off keyword arguments
    heights = kwargs.pop('heights', None)
    bottom = kwargs.pop('bottom', None)
    depth = kwargs.pop('depth', 100 * units.hPa)
    interpolate = kwargs.pop('interpolate', True)

    # If we get the depth kwarg, but it's None, set it to the default as well
    if depth is None:
        depth = 100 * units.hPa

    # Make sure pressure and datavars are the same length
    for datavar in args:
        if len(pressure) != len(datavar):
            raise ValueError('Pressure and data variables must have the same length.')

    # If the bottom is not specified, make it the surface pressure
    if bottom is None:
        bottom = np.nanmax(pressure) * pressure.units

    bottom_pressure, bottom_height = _get_bound_pressure_height(pressure, bottom,
                                                                heights=heights,
                                                                interpolate=interpolate)

    # Calculate the top if whatever units depth is in
    if depth.dimensionality == {'[length]': -1.0, '[mass]': 1.0, '[time]': -2.0}:
        top = bottom_pressure - depth
    elif depth.dimensionality == {'[length]': 1}:
        top = bottom_height + depth
    else:
        raise ValueError('Depth must be specified in units of length or pressure')

    top_pressure, _ = _get_bound_pressure_height(pressure, top, heights=heights,
                                                 interpolate=interpolate)

    ret = []  # returned data variables in layer

    # Ensure pressures are sorted in ascending order
    sort_inds = np.argsort(pressure)
    pressure = pressure[sort_inds]

    # Mask based on top and bottom pressure
    inds = (_less_or_close(pressure, bottom_pressure) &
            _greater_or_close(pressure, top_pressure))
    p_interp = pressure[inds]

    # Interpolate pressures at bounds if necessary and sort
    if interpolate:
        # If we don't have the bottom or top requested, append them
        if not np.any(np.isclose(top_pressure, p_interp)):
            p_interp = np.sort(np.append(p_interp, top_pressure)) * pressure.units
        if not np.any(np.isclose(bottom_pressure, p_interp)):
            p_interp = np.sort(np.append(p_interp, bottom_pressure)) * pressure.units

    ret.append(p_interp[::-1])

    for datavar in args:
        # Ensure that things are sorted in ascending order
        datavar = datavar[sort_inds]

        if interpolate:
            # Interpolate for the possibly missing bottom/top values
            datavar_interp = log_interp(p_interp, pressure, datavar)
            datavar = datavar_interp
        else:
            datavar = datavar[inds]

        ret.append(datavar[::-1])
    return ret


@exporter.export
@units.wraps(None, ('=A', '=A'))
def interp(x, xp, *args, **kwargs):
    r"""Interpolates data with any shape over a specified axis.

    Interpolation over a specified axis for arrays of any shape.

    Parameters
    ----------
    x : array-like
        1-D array of desired interpolated values.

    xp : array-like
        The x-coordinates of the data points.

    args : array-like
        The data to be interpolated. Can be multiple arguments, all must be the same shape as
        xp.

    axis : int, optional
        The axis to interpolate over. Defaults to 0.

    fill_value: float, optional
        Specify handling of interpolation points out of data bounds. If None, will return
        ValueError if points are out of bounds. Defaults to nan.

    Returns
    -------
    array-like
        Interpolated values for each point with coordinates sorted in ascending order.

    Examples
    --------
     >>> x = np.array([1., 2., 3., 4.])
     >>> y = np.array([1., 2., 3., 4.])
     >>> x_interp = np.array([2.5, 3.5])
     >>> metpy.calc.interp(x_interp, x, y)
     array([ 2.5,  3.5])

    Notes
    -----
    xp and args must be the same shape.

    """
    # Pull out keyword args
    fill_value = kwargs.pop('fill_value', np.nan)
    axis = kwargs.pop('axis', 0)

    # Make x an array
    x = np.asanyarray(x).reshape(-1)

    # Save number of dimensions in xp
    ndim = xp.ndim

    # Sort input data
    sort_args = np.argsort(xp, axis=axis)
    sort_x = np.argsort(x)

    # indices for sorting
    sorter = broadcast_indices(xp, sort_args, ndim, axis)

    # sort xp
    xp = xp[sorter]
    # Ensure pressure in increasing order
    variables = [arr[sorter] for arr in args]

    # Make x broadcast with xp
    x_array = x[sort_x]
    expand = [np.newaxis] * ndim
    expand[axis] = slice(None)
    x_array = x_array[expand]

    # Calculate value above interpolated value
    minv = np.apply_along_axis(np.searchsorted, axis, xp, x[sort_x])
    minv2 = np.copy(minv)

    # If fill_value is none and data is out of bounds, raise value error
    if ((np.max(minv) == xp.shape[axis]) or (np.min(minv) == 0)) and fill_value is None:
        raise ValueError('Interpolation point out of data bounds encountered')

    # Warn if interpolated values are outside data bounds, will make these the values
    # at end of data range.
    if np.max(minv) == xp.shape[axis]:
        warnings.warn('Interpolation point out of data bounds encountered')
        minv2[minv == xp.shape[axis]] = xp.shape[axis] - 1
    if np.min(minv) == 0:
        minv2[minv == 0] = 1

    # Get indices for broadcasting arrays
    above = broadcast_indices(xp, minv2, ndim, axis)
    below = broadcast_indices(xp, minv2 - 1, ndim, axis)

    if np.any(x_array < xp[below]):
        warnings.warn('Interpolation point out of data bounds encountered')

    # Create empty output list
    ret = []

    # Calculate interpolation for each variable
    for var in variables:
        var_interp = var[below] + ((x_array - xp[below]) /
                                   (xp[above] - xp[below])) * (var[above] -
                                                               var[below])

        # Set points out of bounds to fill value.
        var_interp[minv == xp.shape[axis]] = fill_value
        var_interp[x_array < xp[below]] = fill_value

        # Check for input points in decreasing order and return output to match.
        if x[0] > x[-1]:
            var_interp = np.swapaxes(np.swapaxes(var_interp, 0, axis)[::-1], 0, axis)
        # Output to list
        ret.append(var_interp)
    if len(ret) == 1:
        return ret[0]
    else:
        return ret


def broadcast_indices(x, minv, ndim, axis):
    """Calculate index values to properly broadcast index array within data array.

    See usage in interp.
    """
    ret = []
    for dim in range(ndim):
        if dim == axis:
            ret.append(minv)
        else:
            broadcast_slice = [np.newaxis] * ndim
            broadcast_slice[dim] = slice(None)
            dim_inds = np.arange(x.shape[dim])
            ret.append(dim_inds[broadcast_slice])
    return ret


@exporter.export
@units.wraps(None, ('=A', '=A'))
def log_interp(x, xp, *args, **kwargs):
    r"""Interpolates data with logarithmic x-scale over a specified axis.

    Interpolation on a logarithmic x-scale for interpolation values in pressure coordintates.

    Parameters
    ----------
    x : array-like
        1-D array of desired interpolated values.

    xp : array-like
        The x-coordinates of the data points.

    args : array-like
        The data to be interpolated. Can be multiple arguments, all must be the same shape as
        xp.

    axis : int, optional
        The axis to interpolate over. Defaults to 0.

    fill_value: float, optional
        Specify handling of interpolation points out of data bounds. If None, will return
        ValueError if points are out of bounds. Defaults to nan.

    Returns
    -------
    array-like
        Interpolated values for each point with coordinates sorted in ascending order.

    Examples
     --------
     >>> x_log = np.array([1e3, 1e4, 1e5, 1e6])
     >>> y_log = np.log(x_log) * 2 + 3
     >>> x_interp = np.array([5e3, 5e4, 5e5])
     >>> metpy.calc.log_interp(x_interp, x_log, y_log)
     array([ 20.03438638,  24.63955657,  29.24472675])

    Notes
    -----
    xp and args must be the same shape.

    """
    # Pull out kwargs
    fill_value = kwargs.pop('fill_value', np.nan)
    axis = kwargs.pop('axis', 0)

    # Log x and xp
    log_x = np.log(x)
    log_xp = np.log(xp)
    return interp(log_x, log_xp, *args, axis=axis, fill_value=fill_value)


def _greater_or_close(a, value, **kwargs):
    r"""Compare values for greater or close to boolean masks.

    Returns a boolean mask for values greater than or equal to a target within a specified
    absolute or relative tolerance (as in :func:`numpy.isclose`).

    Parameters
    ----------
    a : array-like
        Array of values to be compared
    value : float
        Comparison value

    Returns
    -------
    array-like
        Boolean array where values are greater than or nearly equal to value.

    """
    return np.greater(a, value) | np.isclose(a, value, **kwargs)


def _less_or_close(a, value, **kwargs):
    r"""Compare values for less or close to boolean masks.

    Returns a boolean mask for values less than or equal to a target within a specified
    absolute or relative tolerance (as in :func:`numpy.isclose`).

    Parameters
    ----------
    a : array-like
        Array of values to be compared
    value : float
        Comparison value

    Returns
    -------
    array-like
        Boolean array where values are less than or nearly equal to value.

    """
    return np.less(a, value) | np.isclose(a, value, **kwargs)
