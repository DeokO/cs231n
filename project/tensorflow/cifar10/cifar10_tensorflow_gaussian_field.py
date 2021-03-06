import io

import gzip
import os
import re
import sys
import tarfile

from datetime import datetime, date
import time
import urllib.request

import calendar

import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from scipy import ndimage

import tensorflow as tf
from data_utils import get_CIFAR10_data

import seaborn as sns
sns.set_style("darkgrid")
plt.rcParams['figure.figsize'] = (10.0, 8.0) # set default size of plots
plt.rcParams['image.interpolation'] = 'nearest'
plt.rcParams['image.cmap'] = 'gray'

from sklearn.metrics import confusion_matrix
classes = ['plane', 'car', 'bird', 'cat', 'deer', 'dog', 'frog', 'horse', 'ship', 'truck']

# plt.get_cmap('gray')
def plot_confusion_matrix(cm, title='Confusion matrix', cmap=plt.cm.Blues, labels=None):
    if labels is None:
        labels = list(range(len(cm)))
    fig = plt.figure()
    plt_img = plt.imshow(cm, interpolation='nearest', cmap=cmap)
    fig.colorbar(plt_img)
    plt.title(title)
    tick_marks = np.arange(len(labels))
    plt.xticks(tick_marks, labels, rotation=45)
    plt.yticks(tick_marks, labels)
    plt.grid(b='off')
    plt.tight_layout()
    plt.ylabel('True label')
    plt.xlabel('Predicted label')
    #plt.colorbar()
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    return buf

IMAGE_SIZE = 32
NUM_CLASSES = 10
BATCH_SIZE = 512

LOSSES_COLLECTION  = 'regularizer_losses'
DEFAULT_REG_WEIGHT =  5e-2


def activation_summaries(activation, name):
  with tf.name_scope("activation_summaries"):
    mean = tf.reduce_mean(activation)
    tf.summary.histogram(name + '/activations', activation)
    tf.summary.scalar(name + '/sparsity', tf.nn.zero_fraction(activation))
    with tf.name_scope('stddev'):
      stddev = tf.sqrt(tf.reduce_sum(tf.square(activation - mean)))
    tf.summary.scalar('stddev/' + name, stddev)
    tf.summary.scalar('max/' + name, tf.reduce_max(activation))
    tf.summary.scalar('min/' + name, tf.reduce_min(activation))

def variable_summaries(variable, name):
  with tf.name_scope("variable_summaries"):
    mean = tf.reduce_mean(variable)
    tf.summary.histogram(name + '/variable_hist', variable)
    with tf.name_scope('stddev'):
      stddev = tf.sqrt(tf.reduce_sum(tf.square(variable - mean)))
    tf.summary.scalar('stddev/' + name, stddev)
    tf.summary.scalar('max/' + name, tf.reduce_max(variable))
    tf.summary.scalar('min/' + name, tf.reduce_min(variable))

#reg_placeholder = tf.placeholder(dtype=tf.float32, shape=[1])
## may want to add this to the inputs for rcl (and inference methods)
# with tf.op_scope([tensor], scope, 'L2Loss'):
#     weight = tf.convert_to_tensor(weight,
#                               dtype=tensor.dtype.base_dtype,
#                               name='loss_weight')
#     loss = tf.mul(weight, tf.nn.l2_loss(tensor), name='value')
#     tf.add_to_collection(LOSSES_COLLECTION, loss)
#     return loss


# IDEA: construct validation network (reuses parameters)
#       construct train network
#       construct visualization tool
#       construct weight reduction test

#probably need to change for validation
# so: it should be train_layer
# validation_layer = tf.get_variable("W", resuse=True)


def weight_decay(layer_weights, wd=0.99):
  layer_weights = tf.mul(wd, layer_weights)
  return layer_weights

def conv_relu_eval_model(layer_in, name):
  with tf.variable_scope(name, reuse=True) as scope:
    kernel = tf.get_variable("W")
    bias = tf.get_variable("b")
    conv = tf.nn.conv2d(layer_in, kernel, strides=[1,1,1,1], padding='SAME')
    layer = tf.nn.relu(conv + bias)
  return layer

sub = None

def conv_relu(layer_in, kernel_shape, bias_shape, name):
  global sub
  with tf.variable_scope(name) as scope:
    kernel = tf.get_variable("W",
                             shape=kernel_shape,
                             initializer=tf.contrib.layers.xavier_initializer_conv2d())
    bias = tf.get_variable("b", shape=bias_shape, initializer=tf.constant_initializer(0.))

    weight_decayed_op = weight_decay(kernel)

    if name != "conv_1":
      # a_s = np.random.binomial(1, p=0.8, size=kernel_shape[0:3])
      # #np.random.randint(low=0, high=2, size=kernel_shape[0:3])
      # print(name,"\n",a_s[:,:,0])
      # a_s = a_s[:,:,:,np.newaxis]

      # sub = tf.constant(a_s, dtype=tf.float32)
      # kernel = kernel * sub
      #Above is similar to drop connect(?)
      print("Not altering the 'kernels' for later layers")
    else:
      unit_value =  np.zeros(shape=(5,5))
      unit_value[2,2] = 1
      a_s = ndimage.filters.gaussian_filter(unit_value, sigma=1.0, order=0)
      max_a = np.max(a_s)
      a_s = 0.95 * a_s / max_a
      a_s = a_s[:,:, np.newaxis, np.newaxis]
      gaussian_field = tf.constant(a_s, dtype=tf.float32)
      field_modified = tf.random_uniform(shape=kernel_shape, minval=0, maxval=1.0, dtype=tf.float32, name='random')
      sub = tf.greater(field_modified, gaussian_field)
      # a_s = np.array([[0,0,1,0,0],
      #                 [0,1,1,1,0],
      #                 [1,1,1,1,1],
      #                 [0,1,1,1,0],
      #                 [0,0,1,0,0]])
      print(name,"\n", a_s)
      # a_s = a_s[:,:, np.newaxis, np.newaxis]
      # sub = tf.constant(a_s, dtype=tf.float32)
      sub = tf.cast(sub, dtype=tf.float32)
      kernel = kernel * sub

    conv = tf.nn.conv2d(layer_in, kernel, strides=[1,1,1,1], padding='SAME')
    layer = tf.nn.relu(conv + bias)
    #variable_summaries(bias, bias.name)
    variable_summaries(kernel, kernel.name)
    activation_summaries(layer, layer.name)
    # layer = tf.nn.lrn(layer, 4, bias=1.0, alpha=0.001 / 9.0, beta=0.75, name='norm')
  return layer, weight_decayed_op

def fcl_relu_eval_model(layer_in, name):
  with tf.variable_scope(name, reuse=True) as scope:
    dim = np.prod(layer_in.get_shape().as_list()[1:])
    reshape = tf.reshape(layer_in, [-1, dim])
    weights = tf.get_variable("W_fcl")
    bias = tf.get_variable("b_fcl")
    layer = tf.nn.relu(tf.matmul(reshape, weights) + bias)
    layer = tf.nn.dropout(layer, 1.0)
  return layer

def fcl_relu(layer_in, output_size, name,
             regularizer_weight=None, keep_prob = None,
             loss_collection=LOSSES_COLLECTION):
  with tf.variable_scope(name) as scope:
    #batch_size = layer_in.get_shape().as_list()[0]
    dim = np.prod(layer_in.get_shape().as_list()[1:])
    reshape = tf.reshape(layer_in, [-1, dim])
    weights = tf.get_variable("W_fcl",
                              shape=[dim, output_size],
                              initializer=tf.contrib.layers.xavier_initializer())
    bias = tf.get_variable("b_fcl",
                           shape=[output_size],
                           initializer=tf.constant_initializer(0.))
    if keep_prob is None:
      keep_prob = 1.
    layer = tf.nn.relu(tf.matmul(reshape, weights) + bias, name=scope.name + "_activation")
    layer = tf.nn.dropout(layer, keep_prob)
    variable_summaries(weights, weights.name)
    #variable_summaries(bias, bias.name)
    activation_summaries(layer, layer.name)
    if regularizer_weight is None:
      regularizer_weight = DEFAULT_REG_WEIGHT
    regularizer_loss = tf.mul(regularizer_weight, tf.nn.l2_loss(weights))
    tf.add_to_collection(loss_collection, regularizer_loss)
  return layer

def inference_eval_model(images,
                         classes = NUM_CLASSES,
                         model_name="model_1"):
  with tf.variable_scope(model_name, reuse=True) as model_scope:
    layer = conv_relu_eval_model(images, "conv_1")
    layer = conv_relu_eval_model(layer, "conv_2")
    layer = conv_relu_eval_model(layer, "conv_3")
    layer = conv_relu_eval_model(layer, "conv_4")
    layer = conv_relu_eval_model(layer, "conv_5")
    layer = conv_relu_eval_model(layer, "conv_6")
    #layer = conv_relu_eval_model(layer, "conv_7")
    layer = fcl_relu_eval_model(layer, "fcl_1")
    with tf.variable_scope('pre_softmax_linear', reuse=True) as scope:
      weights = tf.get_variable('weights')
      biases = tf.get_variable('biases')
      pre_softmax_linear = tf.add(tf.matmul(layer, weights), biases, name=scope.name)
  return pre_softmax_linear

def predict_eval_model(logits):
  return tf.argmax(logits, dimension=1)

def accuracy_eval_model(logits, y_label):
  return tf.reduce_mean(tf.cast(tf.equal(tf.argmax(logits,1), y_label), tf.float32))

def inference(images,
              classes = NUM_CLASSES,
              keep_prob=None,
              regularizer_weight=None,
              loss_collection=LOSSES_COLLECTION,
              model_name="model_1"):
  with tf.variable_scope(model_name) as model_scope:
    layer, wd_0 = conv_relu(images, [5,5,3,128], [128], "conv_1")
    layer, wd_1 = conv_relu(layer,  [3,3,128,128], [128], "conv_2")
    layer, wd_2 = conv_relu(layer,  [3,3,128,128], [128], "conv_3")
    layer, wd_3 = conv_relu(layer,  [3,3,128,128], [128], "conv_4")
    layer, wd_4 = conv_relu(layer,  [3,3,128,256], [256], "conv_5")
    layer, wd_5 = conv_relu(layer,  [3,3,256,256], [256], "conv_6")
    #layer, wd_6 = conv_relu(layer,  [3,3,256,256], [256], "conv_7")
    # layer = conv_relu(layer,  [5,5,64,64], [64], "conv_4")
    # layer = conv_relu(layer,  [3,3,128,128], [128], "conv_5")
    # layer = conv_relu(layer,  [3,3,128,128], [128], "conv_6")
    layer = fcl_relu(layer, 128, "fcl_1", regularizer_weight=regularizer_weight, keep_prob=keep_prob)

    with tf.variable_scope('pre_softmax_linear') as scope:
      weights = tf.get_variable('weights',
                                shape=[128, classes],
                                initializer=tf.contrib.layers.xavier_initializer())
      biases = tf.get_variable('biases',
                               shape=[classes],
                               initializer=tf.constant_initializer(0.))
      pre_softmax_linear = tf.add(tf.matmul(layer, weights), biases, name=scope.name)
      if keep_prob is None:
        keep_prob = 1.
      pre_softmax_linear = tf.nn.dropout(pre_softmax_linear, keep_prob)
      variable_summaries(weights, weights.name)
      #variable_summaries(biases, biases.name)
      #activation_summaries(pre_softmax_linear, pre_softmax_linear.name)
      if regularizer_weight is None:
        regularizer_weight = DEFAULT_REG_WEIGHT
      regularizer_loss = tf.mul(regularizer_weight, tf.nn.l2_loss(weights))
      tf.add_to_collection(loss_collection, regularizer_loss)
  return pre_softmax_linear, wd_0, wd_1, wd_2

def predict(logits):
  return tf.argmax(logits, dimension=1)

def loss(logits, labels):
  labels = tf.cast(labels, tf.int64)
  cross_entropy = tf.nn.sparse_softmax_cross_entropy_with_logits(
      logits, labels, name='cross_entropy_per_example')
  cross_entropy_mean = tf.reduce_mean(cross_entropy, name='cross_entropy')

  # The total loss is defined as the cross entropy loss
  return cross_entropy_mean

INITIAL_LEARNING_RATE = 0.03
LEARNING_RATE_DECAY_FACTOR = 0.95
DROPOUT_KEEPPROB = 0.9
NUM_EPOCHS_PER_DECAY = 15
MAX_STEPS = 100000

DECAY_STEPS = NUM_EPOCHS_PER_DECAY * 150
#150 is roughly the number of batches per epoch
#40,000/256 ~ 150

#probably should pass in a momentum parameters
def train(total_loss, global_step, learning_rate=INITIAL_LEARNING_RATE):
  lr = tf.train.exponential_decay(learning_rate,
                                  global_step,
                                  DECAY_STEPS,#number of steps required for it to decay
                                  LEARNING_RATE_DECAY_FACTOR,
                                  staircase=True)

  tf.summary.scalar('learning_rate', lr)

  #compute gradient step
  with tf.control_dependencies([total_loss]):
    opt = tf.train.MomentumOptimizer(lr, momentum=0.95)
    grads = opt.compute_gradients(total_loss)

  #if we wanted to clip the gradients
  #would apply the operation here

  #apply the gradients
  apply_gradient_op = opt.apply_gradients(grads, global_step=global_step)

  for grad, var in grads:
    if grad is not None:
      print("Found gradients for: ", var.op.name)
      tf.summary.histogram(var.op.name + "/gradients", grad)

  with tf.control_dependencies([apply_gradient_op]):
    train_op = tf.no_op(name="train")

  #opt = tf.train.GradientDescentOptimizer(lr).minimize(total_loss, global_step=global_step)
  # grads = opt.compute_gradients(total_loss)

  return train_op

def main():
  print("Loading Data;")

  lr = INITIAL_LEARNING_RATE
  reg_weight = DEFAULT_REG_WEIGHT


  data = get_CIFAR10_data()
  for k, v in data.items():
      print('%s: '%(k), v.shape)

  #PLACEHOLDER VARIABLES
  keep_prob = tf.placeholder(dtype=tf.float32, shape=())
  learning_rate = tf.placeholder(dtype=tf.float32, shape=())
  regularizer_weight = tf.placeholder(dtype=tf.float32, shape=())
  #Not used --- ^ (currently)

  X_image = tf.placeholder(dtype=tf.float32, shape=[None, 32, 32, 3])
  y_label = tf.placeholder(dtype=tf.int64, shape=[None])

  #MODEL related operations and values
  global_step = tf.Variable(0, trainable=False)
  #MODEL construction
  logits, wd_0, wd_1, wd_2 = inference(X_image, keep_prob=keep_prob, regularizer_weight=regularizer_weight)
  prediction = predict(logits)
  loss_op = loss(logits, y_label)

  reg_loss = tf.reduce_sum(tf.get_collection(LOSSES_COLLECTION))
  total_loss = loss_op + reg_loss

  accuracy_op = tf.reduce_mean(tf.cast(tf.equal(tf.argmax(logits,1), y_label), tf.float32))
  train_op = train(total_loss, global_step, learning_rate=lr)
  saver = tf.train.Saver(tf.global_variables())

  logits_test = inference_eval_model(X_image)
  accuracy_test = accuracy_eval_model(logits_test, y_label)

  #Summary operation
  tf.summary.image('images', X_image)
  summary_op = tf.summary.merge_all()

  # confusion_img_placeholder = tf.placeholder(dtype=tf.uint8, shape=[1,None,None,4])
  # confusion_matrix_summary  = tf.summary.image('confusion_matrix', confusion_img_placeholder)

  acc_summary        = tf.summary.scalar('Training_accuracy_batch', accuracy_op)
  validation_acc_summary = tf.summary.scalar('Validation_accuracy', accuracy_op)
  cross_entropy_loss = tf.summary.scalar('loss_raw', loss_op)
  reg_loss_summary   = tf.summary.scalar('regularization_loss', reg_loss)
  total_loss_summary = tf.summary.scalar('total_loss', total_loss)

  accuracy_batch = tf.placeholder(shape=(None), dtype=tf.float32)
  overfit_estimate = tf.placeholder(shape=(None), dtype=tf.float32)

  accuracy_100 = tf.reduce_mean(accuracy_batch)
  mean_summary = tf.summary.scalar('Training_accuracy_mean', accuracy_100)
  validation_mean_summary = tf.summary.scalar('Validation_accuracy_mean', accuracy_100)

  acc_summary_histogram = tf.summary.histogram('Training_accuracy_histogram', accuracy_batch)
  overfit_summary = tf.summary.scalar('overfit_estimate', overfit_estimate)

  #SESSION Construction
  init = tf.global_variables_initializer()
  sess = tf.Session(config=tf.ConfigProto(
        log_device_placement=False))
  sess.run(init)

  # print("sub.shape = ", sub.get_shape())
  # print("sub = ", sess.run(sub))
  # print("sub = ", sess.run(sub))
  #today = date.today()
  current_time = datetime.now()
  # LR_%f, INITIAL_LEARNING_RATE
  # REG_%f, DEFAULT_REG_WEIGHT
  # add details, relating per epoch results (and mean filtered loss etc.)
  train_dir = "cifar10_results/LR_" + str(lr) + "/" + "REG_" + str(reg_weight) + "/" + "KP_" + str(DROPOUT_KEEPPROB) + "/" + current_time.strftime("%B") + "_" + str(current_time.day) + "_" + str(current_time.year) + "-h" + str(current_time.hour) + "m" + str(current_time.minute)
  print("Writing summary data to :  ",train_dir)
  summary_writer = tf.summary.FileWriter(train_dir, sess.graph)

  acc_list = []
  valid_acc_list = []

  cm_placeholder = tf.placeholder(shape=(1, None, None, 4), dtype=tf.uint8)
  confusion_summary = tf.summary.image('confusion_matrix', cm_placeholder)

  print("Starting Training.")
  print("Training for %d batches (of size %d); initial learning rate %f" % (MAX_STEPS, BATCH_SIZE, lr))
  for step in range(MAX_STEPS):
    num_train = data['X_train'].shape[0]
    if BATCH_SIZE * (step - 1) // num_train < BATCH_SIZE * (step) // num_train and step > 0:
      print("Completed Epoch: %d (step=%d, MAX_STEPS=%d, percentage complete= %f)" % ((BATCH_SIZE * (step) // num_train ), step, MAX_STEPS, step/MAX_STEPS * 100))
      sess.run([wd_0])

    batch_mask = np.random.choice(num_train, BATCH_SIZE)
    X_batch = data['X_train'][batch_mask]
    y_batch = data['y_train'][batch_mask]
    start_time = time.time()
    feed_dict = { X_image : X_batch, y_label : y_batch, keep_prob : DROPOUT_KEEPPROB, regularizer_weight : reg_weight }

    loss_value, accuracy, acc_str, xentropy_str, reg_loss_str, predicted_class = sess.run([total_loss, accuracy_op, acc_summary, cross_entropy_loss, reg_loss_summary, prediction], feed_dict=feed_dict)
    #print(sess.run(prediction, feed_dict=feed_dict))
    sess.run(train_op, feed_dict=feed_dict)

    acc_list.append(accuracy)
    accuracy_100_str = sess.run(mean_summary, feed_dict={accuracy_batch : np.array(acc_list[-100:])})
    #print(sess.run([accuracy_100], feed_dict={accuracy_batch : np.array(acc_list[-100:])}))
    summary_writer.add_summary(acc_str, step)
    summary_writer.add_summary(xentropy_str, step)
    summary_writer.add_summary(reg_loss_str, step)
    summary_writer.add_summary(accuracy_100_str, step)
    #summary_writer.add_summary('Training_accuracy (Mean)', np.mean(acc_list[-100:]), step)
    assert not np.isnan(loss_value), 'Model diverged with loss = NaN'
    if step % 100 == 0:
      summary_str = sess.run(summary_op, feed_dict=feed_dict)
      summary_writer.add_summary(summary_str, step)
      #reg_weight *= 1.001
    if step % 10 == 0:
      if step > 0:
        confusion_buf = plot_confusion_matrix(confusion_matrix(y_batch, predicted_class),
                                              title='Confusion matrix',
                                              cmap=plt.cm.Blues,
                                              labels=classes)
        img = tf.image.decode_png(confusion_buf.getvalue(), channels=4)
        img = tf.expand_dims(img, 0)
        np_img = img.eval(session=sess)
        # print(img.get_shape())
        # print(img.dtype)

        summary_writer.add_summary(confusion_summary.eval(session=sess, feed_dict={cm_placeholder: np_img}), step)
        plt.close()

        acc_summary_histogram_out = sess.run(acc_summary_histogram, feed_dict={accuracy_batch : np.array(acc_list[-100:])})
        summary_writer.add_summary(acc_summary_histogram_out, step)
      num_valid = data['X_val'].shape[0]
      #batch_valid_mask = np.random.choice(num_valid, BATCH_SIZE)
      X_val_batch = data['X_val']#[batch_valid_mask]
      y_val_batch = data['y_val']#[batch_valid_mask]
      valid_dict = { X_image : X_val_batch, y_label : y_val_batch, keep_prob : 1.0, regularizer_weight : 0.00}
      format_str = ('{0}: step {1:>5d}, loss = {2:2.3f}, accuracy = {3:>3.2f}, accuracy (validation) = {4:>3.2f}')
      valid_summary, valid_acc = sess.run([validation_acc_summary, accuracy_op], feed_dict=valid_dict)
      valid_acc_list.append(valid_acc)

      # Probably should change the slice size to be smaller (10 instead of 100)
      valid_accuracy_100_str = sess.run(validation_mean_summary, feed_dict={accuracy_batch : np.array(valid_acc_list[-10:])})
      print(format_str.format(datetime.now(), step, loss_value, accuracy*100, 100*valid_acc))
      print("Validation accuracy (testing) = ", sess.run(accuracy_test, feed_dict=valid_dict))
      overfit_summary_str = sess.run(overfit_summary, feed_dict = {overfit_estimate : accuracy - valid_acc})
      summary_writer.add_summary(overfit_summary_str, step)
      summary_writer.add_summary(valid_summary, step)
      summary_writer.add_summary(valid_accuracy_100_str, step)

    if (step % 500 == 0 and step > 0) or (step + 1) == MAX_STEPS:
      checkpoint_path = os.path.join(train_dir, current_time.strftime("%B") + "_" + str(current_time.day) + "_" + str(current_time.year) + "-h" + str(current_time.hour) + "m" + str(current_time.minute) + 'model.ckpt')
      print("Checkpoint path = ", checkpoint_path)
      saver.save(sess, checkpoint_path, global_step=step)

  return 0

  # rng_state = np.random.get_state()
  # X_train = np.random.permutation(X_train)
  # np.random.set_state(rng_state)
  # y_train = np.random.permutation(y_train)

if __name__ == '__main__':
  main()
