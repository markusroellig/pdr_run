"""Parameter management for PDR models."""

import math
import random
import logging
import itertools
import numpy as np

from pdr_run.config.default_config import DEFAULT_PARAMETERS, PDR_CONFIG

logger = logging.getLogger('dev')

def compute_mass(rtot, density, alpha, rcore):
    """Compute the cloud mass in solar mass from radius (cm) and density."""
    logger.debug(f"Computing mass from: rtot={rtot:.3e} cm, density={density:.3e}, alpha={alpha}, rcore={rcore}")
    
    try:
        mass = 8.41629e-58 * (
            ((12 * density * math.pi * rtot**3) / (9 - 3 * alpha)) - 
            ((4 * density * math.pi * rtot**3 * rcore**(3 - alpha) * alpha) / (9 - 3 * alpha))
        )
        logger.debug(f"Computed mass: {mass:.3e} solar masses")
        return mass
    except Exception as e:
        logger.error(f"Error computing mass: {str(e)}")
        logger.debug(f"Formula components: term1={(12 * density * math.pi * rtot**3) / (9 - 3 * alpha):.3e}, "
                    f"term2={(4 * density * math.pi * rtot**3 * rcore**(3 - alpha) * alpha) / (9 - 3 * alpha):.3e}")
        raise

def compute_radius(cmass, density, alpha, rcore):
    """Compute the cloud radius (cm) from mass (solar mass) and density."""
    logger.debug(f"Computing radius from: mass={cmass:.3e} solar masses, density={density:.3e}, alpha={alpha}, rcore={rcore}")
    
    try:
        denominator = (
            (12 * density * math.pi) / (9 - 3 * alpha) - 
            (4 * density * math.pi * rcore**(3 - alpha) * alpha) / (9 - 3 * alpha)
        )
        logger.debug(f"Denominator value: {denominator:.3e}")
        
        radius = 1.05916e19 * (cmass / denominator)**(1./3.)
        logger.debug(f"Computed radius: {radius:.3e} cm")
        return radius
    except Exception as e:
        logger.error(f"Error computing radius: {str(e)}")
        logger.debug(f"Formula components: term1={(12 * density * math.pi) / (9 - 3 * alpha):.3e}, "
                    f"term2={(4 * density * math.pi * rcore**(3 - alpha) * alpha) / (9 - 3 * alpha):.3e}")
        raise

def from_string_to_par_log(strg):
    """Convert string parameter to numeric value (log scale)."""
    logger.debug(f"Converting log-scale string parameter to numeric: '{strg}'")
    try:
        value = eval(strg)
        logger.debug(f"Converted value: {value}")
        return value
    except Exception as e:
        logger.error(f"Error converting string '{strg}' to numeric value: {str(e)}")
        raise ValueError(f"Unable to convert '{strg}' to numeric value: {str(e)}")

def from_string_to_par(strg):
    """Convert string parameter to numeric value."""
    logger.debug(f"Converting string parameter to numeric: '{strg}'")
    try:
        exponent = 0.1 * eval(strg)
        value = 10**exponent
        logger.debug(f"Conversion steps: eval('{strg}')={eval(strg)}, exponent={exponent}, result={value:.3e}")
        return value
    except Exception as e:
        logger.error(f"Error converting string '{strg}' to numeric value: {str(e)}")
        raise ValueError(f"Unable to convert '{strg}' to numeric value: {str(e)}")

def from_par_to_string(par):
    """Convert numeric value to string parameter."""
    logger.debug(f"Converting numeric parameter to string: {par}")
    try:
        if par > 0:
            log_value = 10. * math.log10(par)
            strg = str(round(log_value))
            logger.debug(f"Conversion steps: log10({par})={math.log10(par):.4f}, 10*log10={log_value:.2f}, rounded={round(log_value)}")
        else:
            strg = "-99"
            logger.debug("Parameter <= 0, using default string '-99'")
        
        if strg == "0":
            result = "00"
            logger.debug("String is '0', converting to '00'")
        else:
            result = strg
        
        logger.debug(f"Converted string: '{result}'")
        return result
    except Exception as e:
        logger.error(f"Error converting numeric {par} to string: {str(e)}")
        return "-99"  # Safe default

def from_par_to_string_log(par):
    """Convert numeric value to string parameter (log scale)."""
    logger.debug(f"Converting numeric parameter to log-scale string: {par}")
    try:
        strg = str(par)
        
        # Format string with leading zeros
        if par < 0 and par > -10:
            strg = strg.replace("-", "-0")
            logger.debug(f"Added leading zero for negative single-digit: {strg}")
        if par > 0 and par < 10:
            strg = "0" + strg
            logger.debug(f"Added leading zero for positive single-digit: {strg}")
        
        if strg == "0":
            result = "00"
            logger.debug("String is '0', converting to '00'")
        else:
            result = strg
            
        logger.debug(f"Converted string: '{result}'")
        return result
    except Exception as e:
        logger.error(f"Error converting numeric {par} to log-scale string: {str(e)}")
        return "00"  # Safe default

def string_to_list(strg):
    """Split string on whitespace to create a list."""
    logger.debug(f"Splitting string into list: '{strg}'")
    result = strg.split()
    logger.debug(f"Split result ({len(result)} items): {result}")
    return result

def list_to_string(lst):
    """Join list elements with whitespace."""
    if not lst:
        logger.warning("Empty list provided to list_to_string")
        return ""
        
    logger.debug(f"Joining list to string: {lst}")
    result = " ".join(lst)
    logger.debug(f"Joined result: '{result}'")
    return result

def random_parameter_list(par_range, num, seed=None):
    """Generate a list of random parameters within a given range."""
    logger.info(f"Generating {num} random parameters in range {par_range}")
    
    if seed is not None:
        logger.debug(f"Using random seed: {seed}")
        random.seed(seed)
    
    if len(par_range) == 1:
        logger.debug(f"Single value in range, duplicating value {par_range[0]} {num} times")
        return par_range * num
    
    try:
        nums = [from_string_to_par_log(p) for p in par_range]
        lownum = min(nums)
        upnum = max(nums)
        logger.debug(f"Parameter range: {lownum} to {upnum} (converted from {par_range})")
        
        randomlist = []
        for i in range(num):
            n = random.randint(lownum, upnum)
            param_str = from_par_to_string_log(n)
            randomlist.append(param_str)
            logger.debug(f"Generated random parameter #{i+1}: {n} -> '{param_str}'")
        
        logger.info(f"Generated {len(randomlist)} random parameters: {randomlist[:5]}{'...' if len(randomlist) > 5 else ''}")
        return randomlist
    except Exception as e:
        logger.error(f"Error generating random parameters: {str(e)}")
        raise

def generate_parameter_combinations(config=None):
    """Generate all parameter combinations for model runs."""
    logger.info("Generating parameter combinations")
    
    if config is None:
        logger.info("No config provided, using default parameters")
        config = DEFAULT_PARAMETERS
    else:
        logger.debug(f"Using provided config with keys: {list(config.keys())}")
    
    # Log parameter counts
   # for key in ['metal', 'dens', 'mass', 'chi', 'col']:
    for key in ['metal', 'dens', 'mass', 'chi']:
        if key in config:
            logger.debug(f"Parameter '{key}' has {len(config[key])} values: {config[key]}")
    
    try:
        if config.get('create_random_models', False):
            logger.info("Generating random parameter combinations")
            num = config.get('random_model_num', 50)
            logger.info(f"Number of random models to generate: {num}")
            
            metal_list = random_parameter_list(config['metal'], num)
            dens_list = random_parameter_list(config['dens'], num)
            mass_list = random_parameter_list(config['mass'], num)
            chi_list = random_parameter_list(config['chi'], num)
           # col_list = random_parameter_list(config['col'], num)
            
            combinations = np.array([
                metal_list, dens_list, mass_list, chi_list#, col_list
            ]).transpose()
            
            result = combinations.tolist()
            logger.info(f"Generated {len(result)} random parameter combinations")
            logger.debug(f"First 3 combinations: {result[:3]}")
            logger.debug(f"Last 3 combinations: {result[-3:] if len(result) >= 3 else result}")
            return result
        else:
            logger.info("Generating all parameter combinations")
            
            # Calculate expected number of combinations for validation
            expected_count = (
                len(config['metal']) * 
                len(config['dens']) * 
                len(config['mass']) * 
                len(config['chi']) #* 
               # len(config['col'])
            )
            logger.debug(f"Expected number of combinations: {expected_count}")
            
            combinations = list(itertools.product(
                config['metal'],
                config['dens'],
                config['mass'],
                config['chi']#,
                #config['col']
            ))
            
            logger.info(f"Generated {len(combinations)} parameter combinations")
            #logger.debug(f"First 3 combinations: {combinations[:3]}")
            #logger.debug(f"Last 3 combinations: {combinations[-3:] if len(combinations) >= 3 else combinations}")

            
            # Validate combination count
            if len(combinations) != expected_count:
                logger.warning(f"Generated combination count ({len(combinations)}) differs from expected ({expected_count})")
            
            if combinations:
                logger.debug(f"First 3 combinations: {combinations[:3]}")
                logger.debug(f"Last 3 combinations: {combinations[-3:] if len(combinations) >= 3 else combinations}")
            else:
                logger.warning("No parameter combinations were generated")
                
            return combinations
    except Exception as e:
        logger.error(f"Error generating parameter combinations: {str(e)}", exc_info=True)
        raise